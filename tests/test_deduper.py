import pytest
import tempfile
import os
from app.deduper import LeadDeduper
from app.models import LeadBase


@pytest.fixture
def temp_deduper():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    deduper = LeadDeduper(db_path=db_path)
    yield deduper
    if os.path.exists(db_path):
        os.remove(db_path)


def test_domain_normalization():
    assert LeadDeduper.normalize_domain("https://www.ApexCommercial.com/about?src=google") == "apexcommercial.com"
    assert LeadDeduper.normalize_domain("http://lone-star-realty.co.uk:8080/listings") == "lone-star-realty.co.uk"
    assert LeadDeduper.normalize_domain("www.subdomain.austinhomes.org/") == "subdomain.austinhomes.org"
    assert LeadDeduper.normalize_domain("apexcommercial.com") == "apexcommercial.com"
    assert LeadDeduper.normalize_domain(None) is None


def test_phone_normalization():
    assert LeadDeduper.normalize_phone("(512) 555-0142") == "(512) 555-0142"
    assert LeadDeduper.normalize_phone("512.555.0142") == "(512) 555-0142"
    assert LeadDeduper.normalize_phone("1-512-555-0142") == "(512) 555-0142"
    assert LeadDeduper.normalize_phone("+1 512 555 0142") == "(512) 555-0142"


def test_deduplication_exact_and_fuzzy(temp_deduper):
    lead1 = LeadBase(
        name="Apex Commercial Realty LLC",
        category="Commercial Brokerage",
        phone="(512) 555-0142",
        website="https://www.apexcommercial.com",
        city="Austin",
        state="TX"
    )
    record1, is_new1 = temp_deduper.check_and_upsert(lead1)
    assert is_new1 is True
    assert record1.times_seen == 1
    assert record1.domain == "apexcommercial.com"

    # Same business with slightly different name formatting and URL parameters
    lead2 = LeadBase(
        name="Apex Commercial Realty (Downtown)",
        category="Commercial Real Estate",
        phone="512.555.0142",
        website="https://apexcommercial.com/listings?utm_source=adwords",
        email="contact@apexcommercial.com",  # newly discovered email!
        city="Austin",
        state="TX"
    )
    record2, is_new2 = temp_deduper.check_and_upsert(lead2)
    assert is_new2 is False
    assert record2.id == record1.id
    assert record2.times_seen == 2
    assert record2.email == "contact@apexcommercial.com"  # Enriched!

    stats = temp_deduper.get_stats()
    assert stats["total_leads_in_db"] == 1
    assert stats["total_duplicates_filtered"] == 1
    assert stats["deduplication_rate_percent"] == 50.0
