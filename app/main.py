import asyncio
import uuid
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks, HTTPException, Header, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.models import (
    LeadBase,
    LeadCreate,
    LeadRecord,
    ScrapeJobRequest,
    ScrapeJobResponse,
    JobStatusResponse,
    StatsResponse
)
from app.deduper import LeadDeduper
from app.scraper import RealEstateScraper
from app.integrations.google_sheets import GoogleSheetsClient
from app.integrations.slack import SlackNotifier
from app.mock_server import mock_router

# Setup Structured Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("LeadPulse.Main")

# Global In-Memory Job Registry
JOBS: Dict[str, Dict[str, Any]] = {}

# Singletons with lazy-initialization
_deduper: Optional[LeadDeduper] = None
_sheets_client: Optional[GoogleSheetsClient] = None
_slack_notifier: Optional[SlackNotifier] = None


def get_deduper() -> LeadDeduper:
    global _deduper
    if _deduper is None:
        _deduper = LeadDeduper()
    return _deduper


def get_sheets_client() -> GoogleSheetsClient:
    global _sheets_client
    if _sheets_client is None:
        _sheets_client = GoogleSheetsClient()
    return _sheets_client


def get_slack_notifier() -> SlackNotifier:
    global _slack_notifier
    if _slack_notifier is None:
        _slack_notifier = SlackNotifier()
    return _slack_notifier


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure database & data directories are warm
    logger.info(f"Starting {settings.APP_NAME} in {settings.APP_ENV} mode...")
    app.state.deduper = get_deduper()
    app.state.sheets_client = get_sheets_client()
    app.state.slack_notifier = get_slack_notifier()
    logger.info("Storage and Integrations initialized successfully.")
    yield
    logger.info("Shutting down LeadPulse...")


app = FastAPI(
    title=settings.APP_NAME,
    description="Autonomous B2B Real Estate Lead Scraping, Multi-factor Deduplication, and Sync Pipeline.",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for dashboards / webhooks
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Mock Directory Router for offline testability & instant live demos
app.include_router(mock_router)


async def execute_scraping_pipeline(job_id: str, request: ScrapeJobRequest):
    """
    Background worker that runs the full end-to-end flow:
    1. Async Playwright crawl
    2. Deep contact enrichment
    3. Multi-factor deduplication
    4. Google Sheets API push
    5. Slack Webhook dispatch
    """
    job = JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = datetime.now(timezone.utc).isoformat()
    start_time = time.time()

    deduper: LeadDeduper = getattr(app.state, "deduper", None) or get_deduper()
    sheets_client: GoogleSheetsClient = getattr(app.state, "sheets_client", None) or get_sheets_client()
    slack_notifier: SlackNotifier = getattr(app.state, "slack_notifier", None) or get_slack_notifier()

    try:
        logger.info(f"[{job_id}] Launching Playwright crawler for query='{request.query}' in '{request.location}'...")
        scraper = RealEstateScraper(headless=settings.PLAYWRIGHT_HEADLESS)

        # Execute Playwright async scrape
        raw_leads: List[LeadBase] = await scraper.scrape(
            query=request.query,
            location=request.location,
            max_pages=request.max_pages,
            target_url=request.target_url,
            use_mock=request.use_mock,
            enrich_contacts=request.enrich_contacts
        )

        job["total_found"] = len(raw_leads)
        logger.info(f"[{job_id}] Playwright scraped {len(raw_leads)} agency records. Processing deduplication...")

        new_leads: List[LeadRecord] = []
        duplicate_count = 0

        # Run each scraped lead through deduplication store
        for raw_lead in raw_leads:
            lead_record, is_new = deduper.check_and_upsert(raw_lead)
            if is_new:
                new_leads.append(lead_record)
            else:
                duplicate_count += 1

        job["new_leads_saved"] = len(new_leads)
        job["duplicates_filtered"] = duplicate_count
        job["leads"] = [lead.model_dump() for lead in new_leads]

        logger.info(f"[{job_id}] Deduplication Results: {len(new_leads)} NEW leads, {duplicate_count} DUPLICATES filtered.")

        # Push clean new leads to Google Sheets API
        if request.sync_to_sheets and new_leads:
            sheets_result = await sheets_client.push_leads(new_leads)
            for lead in new_leads:
                deduper.mark_synced(lead.id, sheets=True)
            logger.info(f"[{job_id}] Google Sheets Sync: {sheets_result}")

        # Send Slack Webhook notifications
        if request.notify_slack:
            # 1. Alert for each new high-value agency
            for lead in new_leads:
                await slack_notifier.send_lead_alert(lead)
                deduper.mark_synced(lead.id, slack=True)

            # 2. Overall run summary
            duration = time.time() - start_time
            await slack_notifier.send_summary(
                query=request.query,
                location=request.location,
                total_found=len(raw_leads),
                new_leads=len(new_leads),
                duplicates=duplicate_count,
                duration=duration
            )

        duration = time.time() - start_time
        job["duration_seconds"] = round(duration, 2)
        job["status"] = "completed"
        job["completed_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(f"[{job_id}] Pipeline completed in {duration:.2f} seconds.")

    except Exception as e:
        logger.exception(f"[{job_id}] Scraping job failed: {e}")
        job["status"] = "failed"
        job["error"] = str(e)
        job["duration_seconds"] = round(time.time() - start_time, 2)


# ==========================================
# API ENDPOINTS
# ==========================================

def verify_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_webhook_token: Optional[str] = Header(None, alias="X-Webhook-Token"),
) -> None:
    """Verifies X-API-Key or X-Webhook-Token if API_KEY is configured in settings."""
    expected_key = settings.API_KEY or settings.WEBHOOK_SECRET_TOKEN
    if expected_key:
        provided = x_api_key or x_webhook_token
        if not provided or provided != expected_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: Invalid or missing X-API-Key header. Provide a valid key to access webhook endpoints."
            )


@app.get("/health", tags=["System"])
async def health_check():
    """System health check and configuration verification."""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": "1.0.0",
        "playwright_headless": settings.PLAYWRIGHT_HEADLESS,
        "api_key_auth_enabled": bool(settings.API_KEY or settings.WEBHOOK_SECRET_TOKEN),
        "slack_webhook_configured": bool(settings.SLACK_WEBHOOK_URL),
        "google_sheets_configured": bool(settings.GOOGLE_SERVICE_ACCOUNT_FILE),
        "simulation_fallback_active": settings.ENABLE_SIMULATION_FALLBACK
    }


@app.post("/webhook/trigger", response_model=ScrapeJobResponse, status_code=status.HTTP_202_ACCEPTED, tags=["Webhooks"])
async def trigger_scraping_webhook(
    request: ScrapeJobRequest,
    background_tasks: BackgroundTasks,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_webhook_token: Optional[str] = Header(None, alias="X-Webhook-Token")
):
    """
    Webhook Receiver: Triggers an autonomous scraping and deduplication job on-demand.
    Returns HTTP 202 Accepted with job_id immediately and executes Playwright in background.
    Protected by X-API-Key header when API_KEY is configured in .env.
    """
    verify_api_key(x_api_key=x_api_key, x_webhook_token=x_webhook_token)

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "query": request.query,
        "location": request.location,
        "total_found": 0,
        "new_leads_saved": 0,
        "duplicates_filtered": 0,
        "duration_seconds": None,
        "leads": [],
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    # Queue background task so caller gets immediate HTTP 202 response
    background_tasks.add_task(execute_scraping_pipeline, job_id, request)

    return ScrapeJobResponse(
        job_id=job_id,
        status="queued",
        created_at=JOBS[job_id]["created_at"],
        message=f"Scrape job queued for '{request.query}' in '{request.location}'.",
        poll_url=f"/jobs/{job_id}"
    )


@app.get("/jobs/{job_id}", response_model=JobStatusResponse, tags=["Jobs"])
async def get_job_status(job_id: str):
    """Check the real-time execution status and metrics of a scraping job."""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobStatusResponse(**job)


@app.post("/webhook/ingest", tags=["Webhooks"])
async def ingest_lead_webhook(
    lead: LeadCreate,
    sync_to_sheets: bool = True,
    notify_slack: bool = True,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_webhook_token: Optional[str] = Header(None, alias="X-Webhook-Token")
):
    """
    Webhook Ingestion: Ingests an individual lead directly (from Zapier, Typeform, or external CRM)
    for instant multi-factor deduplication, Google Sheets sync, and Slack notification.
    Protected by X-API-Key header when API_KEY is configured in .env.
    """
    verify_api_key(x_api_key=x_api_key, x_webhook_token=x_webhook_token)

    deduper: LeadDeduper = getattr(app.state, "deduper", None) or get_deduper()
    sheets_client: GoogleSheetsClient = getattr(app.state, "sheets_client", None) or get_sheets_client()
    slack_notifier: SlackNotifier = getattr(app.state, "slack_notifier", None) or get_slack_notifier()

    record, is_new = deduper.check_and_upsert(lead)

    response_data = {
        "status": "success",
        "is_new": is_new,
        "dedupe_key": record.dedupe_key,
        "lead_id": record.id,
        "times_seen": record.times_seen
    }

    if is_new:
        if sync_to_sheets:
            await sheets_client.push_leads([record])
            deduper.mark_synced(record.id, sheets=True)
        if notify_slack:
            await slack_notifier.send_lead_alert(record)
            deduper.mark_synced(record.id, slack=True)
        response_data["action"] = "created_and_synced"
    else:
        response_data["action"] = "duplicate_updated"

    return response_data


@app.get("/leads", response_model=List[LeadRecord], tags=["Leads"])
async def list_leads(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    city: Optional[str] = Query(None),
    state: Optional[str] = Query(None)
):
    """Query clean deduplicated leads from SQLite with optional filtering."""
    deduper: LeadDeduper = getattr(app.state, "deduper", None) or get_deduper()
    return deduper.get_all(limit=limit, offset=offset, city=city, state=state)


@app.get("/leads/export/csv", tags=["Leads"])
async def export_leads_csv():
    """Export all deduplicated leads as a downloadable clean CSV file."""
    deduper: LeadDeduper = getattr(app.state, "deduper", None) or get_deduper()
    leads = deduper.get_all(limit=10000)

    import io
    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Name", "Category", "Domain", "Phone", "Email",
        "Address", "City", "State", "Zip", "Rating", "Reviews", "Website", "Times Seen", "Discovered"
    ])
    for l in leads:
        writer.writerow([
            l.id, l.name, l.category, l.domain or "", l.phone or "", l.email or "",
            l.address or "", l.city or "", l.state or "", l.zip_code or "",
            l.rating or "", l.review_count or 0, l.website or "", l.times_seen, l.first_seen_at
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=clean_real_estate_leads.csv"}
    )


@app.get("/stats", response_model=StatsResponse, tags=["Analytics"])
async def get_deduplication_stats():
    """Returns real-time deduplication metrics and database totals."""
    deduper: LeadDeduper = getattr(app.state, "deduper", None) or get_deduper()
    raw_stats = deduper.get_stats()
    recent = deduper.get_all(limit=5)
    return StatsResponse(
        total_leads_in_db=raw_stats["total_leads_in_db"],
        total_unique_domains=raw_stats["total_unique_domains"],
        total_new_leads_this_session=raw_stats["total_leads_in_db"],
        total_duplicates_filtered=raw_stats["total_duplicates_filtered"],
        deduplication_rate_percent=raw_stats["deduplication_rate_percent"],
        recent_leads=recent
    )
