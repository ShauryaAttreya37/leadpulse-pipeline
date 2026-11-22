import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "LeadPulse" in data["app"]


@pytest.mark.asyncio
async def test_ingest_lead_webhook():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "name": "Caprock Commercial Real Estate",
            "category": "Land & Industrial",
            "phone": "(512) 555-9988",
            "email": "info@caprockrealty.com",
            "website": "https://www.caprockrealty.com",
            "city": "Austin",
            "state": "TX"
        }
        # Ingest first time -> Created
        resp1 = await client.post("/webhook/ingest", json=payload)
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["status"] == "success"
        assert data1["is_new"] is True

        # Ingest duplicate -> Caught and updated
        resp2 = await client.post("/webhook/ingest", json=payload)
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["is_new"] is False
        assert data2["action"] == "duplicate_updated"


@pytest.mark.asyncio
async def test_stats_and_leads_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Ingest a lead first so the isolated database has records
        payload = {
            "name": "Caprock Commercial Real Estate",
            "category": "Land & Industrial",
            "phone": "(512) 555-9988",
            "email": "info@caprockrealty.com",
            "website": "https://www.caprockrealty.com",
            "city": "Austin",
            "state": "TX"
        }
        ingest_resp = await client.post("/webhook/ingest", json=payload)
        assert ingest_resp.status_code == 200

        stats_resp = await client.get("/stats")
        assert stats_resp.status_code == 200
        stats = stats_resp.json()
        assert stats["total_leads_in_db"] >= 1

        leads_resp = await client.get("/leads?limit=10")
        assert leads_resp.status_code == 200
        leads = leads_resp.json()
        assert isinstance(leads, list)
        assert len(leads) >= 1

        csv_resp = await client.get("/leads/export/csv")
        assert csv_resp.status_code == 200
        assert "text/csv" in csv_resp.headers["content-type"]
        assert "Caprock Commercial Real Estate" in csv_resp.text
