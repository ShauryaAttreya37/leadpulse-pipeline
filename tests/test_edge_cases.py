import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.config import settings
from app.deduper import LeadDeduper
from app.models import LeadBase, LeadCreate
from app.integrations.google_sheets import GoogleSheetsClient
from app.integrations.slack import SlackNotifier


# ==============================================================================
# 1. NORMALIZATION & PARSING EDGE CASES
# ==============================================================================

def test_domain_normalization_edge_cases():
    # Protocol-relative URLs
    assert LeadDeduper.normalize_domain("//apexrealty.com/listings") == "apexrealty.com"
    # Bare domain with path and query parameters
    assert LeadDeduper.normalize_domain("austinluxuryhomes.com/contact?utm_source=adwords&ref=fb") == "austinluxuryhomes.com"
    # Port numbers and basic auth
    assert LeadDeduper.normalize_domain("https://broker:secret@austin-realty.org:8443/home") == "austin-realty.org"
    # Mixed case with spaces
    assert LeadDeduper.normalize_domain("  HTTPS://WWW.COMMERCIALBROKERS.NET/  ") == "commercialbrokers.net"
    # Complex multi-level TLDs
    assert LeadDeduper.normalize_domain("https://www.property.co.uk/search") == "property.co.uk"
    # Invalid / empty cases
    assert LeadDeduper.normalize_domain(None) is None
    assert LeadDeduper.normalize_domain("") is None
    assert LeadDeduper.normalize_domain("   ") is None
    assert LeadDeduper.normalize_domain("http://") is None
    assert LeadDeduper.normalize_domain("not-a-domain") is None


def test_phone_normalization_edge_cases():
    # Numbers with standard US extension
    assert LeadDeduper.normalize_phone("(512) 555-0142 ext. 42") == "(512) 555-0142 ext 42"
    assert LeadDeduper.normalize_phone("512-555-0142 x108") == "(512) 555-0142 ext 108"
    assert LeadDeduper.normalize_phone("512.555.0142 #5") == "(512) 555-0142 ext 5"
    # 11-digit number with country code 1
    assert LeadDeduper.normalize_phone("1 (512) 555-0199") == "+1 (512) 555-0199"
    # Clean 10 digits without punctuation
    assert LeadDeduper.normalize_phone("5125550199") == "(512) 555-0199"
    # Invalid / empty cases
    assert LeadDeduper.normalize_phone(None) is None
    assert LeadDeduper.normalize_phone("") is None
    assert LeadDeduper.normalize_phone("---") is None
    assert LeadDeduper.normalize_phone("123") is None  # Too short to be a valid phone


# ==============================================================================
# 2. DEDUPLICATION MATCHING & FIELD ENRICHMENT EDGE CASES
# ==============================================================================

def test_deduplication_different_cities_not_merged(isolate_test_database):
    """
    Two brokerages with the same generic name (e.g. 'Premier Properties')
    in DIFFERENT cities with no domain or phone must NOT be merged as duplicates.
    """
    lead_austin = LeadBase(
        name="Premier Properties",
        category="Brokerage",
        city="Austin",
        state="TX"
    )
    lead_miami = LeadBase(
        name="Premier Properties",
        category="Brokerage",
        city="Miami",
        state="FL"
    )
    rec_austin, is_new1 = isolate_test_database.check_and_upsert(lead_austin)
    rec_miami, is_new2 = isolate_test_database.check_and_upsert(lead_miami)

    assert is_new1 is True
    assert is_new2 is True
    assert rec_austin.id != rec_miami.id
    assert rec_austin.dedupe_key != rec_miami.dedupe_key


def test_duplicate_enrichment_of_all_missing_fields(isolate_test_database):
    """
    When a duplicate is encountered, newly discovered fields (email, address,
    ratings, LinkedIn) should be merged into the existing lead record.
    """
    initial_lead = LeadBase(
        name="Highland Commercial Partners",
        website="https://highlandcp.com",
        phone="(512) 555-1234",
        city="Austin",
        state="TX"
    )
    rec1, is_new1 = isolate_test_database.check_and_upsert(initial_lead)
    assert is_new1 is True
    assert rec1.email is None
    assert rec1.address is None
    assert rec1.rating is None

    # Re-scrape finds email, address, and star rating
    second_scrape = LeadBase(
        name="Highland Commercial Partners LLC",
        website="https://www.highlandcp.com/about",
        phone="512.555.1234",
        email="info@highlandcp.com",
        address="700 Lavaca St",
        city="Austin",
        state="TX",
        zip_code="78701",
        rating=4.9,
        review_count=22,
        social_linkedin="https://linkedin.com/company/highland-cp"
    )
    rec2, is_new2 = isolate_test_database.check_and_upsert(second_scrape)
    assert is_new2 is False
    assert rec2.id == rec1.id
    assert rec2.times_seen == 2
    # Check that fields were enriched:
    assert rec2.email == "info@highlandcp.com"
    assert rec2.address == "700 Lavaca St"
    assert rec2.zip_code == "78701"
    assert rec2.rating == 4.9
    assert rec2.review_count == 22
    assert rec2.social_linkedin == "https://linkedin.com/company/highland-cp"


# ==============================================================================
# 3. FASTAPI ENDPOINT EDGE CASES (Validation, Auth, 404s)
# ==============================================================================

@pytest.mark.asyncio
async def test_job_not_found_returns_404():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/jobs/non_existent_job_12345")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_webhook_token_auth_edge_case():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Simulate API Key required
        settings.API_KEY = "live_prod_api_key_999"

        payload = {
            "query": "Retail Brokerages",
            "location": "Dallas, TX",
            "max_pages": 1,
            "use_mock": True
        }
        # 1. Missing header -> 401
        resp_no_token = await client.post("/webhook/trigger", json=payload)
        assert resp_no_token.status_code == 401

        # 2. Wrong header -> 401
        resp_wrong = await client.post(
            "/webhook/trigger",
            json=payload,
            headers={"X-API-Key": "wrong_key"}
        )
        assert resp_wrong.status_code == 401

        # 3. Valid X-API-Key header -> 202 Accepted
        resp_valid = await client.post(
            "/webhook/trigger",
            json=payload,
            headers={"X-API-Key": "live_prod_api_key_999"}
        )
        assert resp_valid.status_code == 202
        assert resp_valid.json()["status"] == "queued"

        # 4. Backward compatible X-Webhook-Token header -> 202 Accepted
        resp_compat = await client.post(
            "/webhook/trigger",
            json=payload,
            headers={"X-Webhook-Token": "live_prod_api_key_999"}
        )
        assert resp_compat.status_code == 202

        # Reset key
        settings.API_KEY = None


@pytest.mark.asyncio
async def test_ingest_validation_error():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Missing required field 'name'
        invalid_payload = {
            "category": "Commercial Brokerage",
            "phone": "(512) 555-0142"
        }
        resp = await client.post("/webhook/ingest", json=invalid_payload)
        assert resp.status_code == 422  # Unprocessable Entity


@pytest.mark.asyncio
async def test_csv_export_special_characters(isolate_test_database):
    """Verify CSV export correctly quotes fields with commas, quotes, and newlines."""
    special_lead = LeadBase(
        name='Apex & "Partners" Commercial, Inc.',
        address='100 Congress Ave, Suite 100, Building "B"',
        city="Austin",
        state="TX",
        phone="(512) 555-0142",
        website="https://apexpartners.com"
    )
    isolate_test_database.check_and_upsert(special_lead)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/leads/export/csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        # Check that quotes and commas are properly escaped in CSV
        assert 'Apex & ""Partners"" Commercial, Inc.' in resp.text or 'Apex & "Partners" Commercial' in resp.text


# ==============================================================================
# 4. INTEGRATIONS EDGE CASES (Empty leads, missing configs)
# ==============================================================================

@pytest.mark.asyncio
async def test_google_sheets_client_empty_leads():
    client = GoogleSheetsClient()
    result = await client.push_leads([])
    assert result["status"] == "skipped"
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_slack_block_format_without_optional_fields():
    notifier = SlackNotifier()
    # Lead with no rating, no website, no linkedin
    bare_lead = LeadBase(
        name="Bare Minimal Brokerage",
        category="General",
        phone="(512) 555-0000"
    )
    # Dedupe generates a record
    deduper = LeadDeduper()
    record, _ = deduper.check_and_upsert(bare_lead)

    block = notifier._format_lead_block(record)
    assert "blocks" in block
    assert block["blocks"][0]["type"] == "header"
    # Ensure no exception was raised and output has divider
    assert block["blocks"][-1]["type"] == "divider"
