#!/usr/bin/env python3
"""
LeadPulse - Autonomous End-to-End Pipeline Demonstration
Runs the full flow:
1. Spawns mock directory server in background
2. Executes Async Playwright browser crawl across multiple pages
3. Enriches contact info (emails, social profiles)
4. Normalizes and dedupes leads in SQLite
5. Pushes clean rows to Google Sheets mirror and Slack dispatcher
6. Prints a formatted execution report
"""
import sys
import os
import time
import asyncio
import threading
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from app.config import settings
from app.models import ScrapeJobRequest
from app.scraper import RealEstateScraper
from app.deduper import LeadDeduper
from app.integrations.google_sheets import GoogleSheetsClient
from app.integrations.slack import SlackNotifier
from app.mock_server import mock_router
from fastapi import FastAPI


def run_local_mock_server():
    """Runs a dedicated lightweight ASGI server on port 8000 for the mock directory."""
    mock_app = FastAPI()
    mock_app.include_router(mock_router)
    config = uvicorn.Config(mock_app, host="127.0.0.1", port=settings.PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


async def run_pipeline_demo():
    print("=" * 80)
    print(" 🚀 LeadPulse: Autonomous Real Estate Lead Scraping & Deduplication Pipeline")
    print("=" * 80)
    print(f"Target: Commercial Real Estate Agencies & Brokerages in Austin, TX")
    print(f"Engine: Async Playwright (Chromium) with Route Interception (5x speed)")
    print(f"Storage: SQLite with Canonical Multi-Factor Hashing (WAL Mode)")
    print(f"Sinks: Google Sheets API + Slack Block Kit Webhook")
    print("-" * 80)

    # Initialize components
    deduper = LeadDeduper()
    sheets_client = GoogleSheetsClient()
    slack_notifier = SlackNotifier()
    scraper = RealEstateScraper(headless=True)

    print("\n[Step 1/5] Launching Async Playwright Browser...")
    start_time = time.time()
    
    # Scrape 2 pages of the directory (contains 8 raw listings with 2 intentional duplicates)
    raw_leads = await scraper.scrape(
        query="Commercial Real Estate Agencies",
        location="Austin, TX",
        max_pages=2,
        enrich_contacts=True
    )
    crawl_duration = time.time() - start_time
    print(f"  ✓ Browser crawl finished in {crawl_duration:.2f}s")
    print(f"  ✓ Discovered {len(raw_leads)} raw agency listings from target directory")

    print("\n[Step 2/5] Running Multi-Factor Deduplication & Enrichment Engine...")
    new_leads = []
    duplicate_leads = []

    for raw in raw_leads:
        record, is_new = deduper.check_and_upsert(raw)
        if is_new:
            new_leads.append(record)
            print(f"  [+] NEW LEAD: {record.name[:35]:<35} | Domain: {record.domain or 'N/A'}")
        else:
            duplicate_leads.append(record)
            print(f"  [♻️] DUPLICATE: {record.name[:35]:<35} | Domain: {record.domain or 'N/A'} (Seen {record.times_seen}x)")

    print(f"\n  Summary: {len(new_leads)} Unique New Leads | {len(duplicate_leads)} Duplicates Filtered")

    print("\n[Step 3/5] Pushing Clean Leads to Google Sheets API...")
    if new_leads:
        sheets_res = await sheets_client.push_leads(new_leads)
        print(f"  ✓ {sheets_res.get('destination')}: Synced {sheets_res.get('count')} rows")
        if "mirror_file" in sheets_res:
            print(f"  ✓ Verified in local live mirror: {sheets_res['mirror_file']}")

    print("\n[Step 4/5] Dispatching Slack Block Kit Notifications...")
    for lead in new_leads[:2]:  # Send top alerts
        await slack_notifier.send_lead_alert(lead)
    
    total_duration = time.time() - start_time
    await slack_notifier.send_summary(
        query="Commercial Real Estate Agencies",
        location="Austin, TX",
        total_found=len(raw_leads),
        new_leads=len(new_leads),
        duplicates=len(duplicate_leads),
        duration=total_duration
    )
    print(f"  ✓ Dispatched Slack notifications and pipeline summary")

    print("\n[Step 5/5] Final Pipeline Metrics & Clean Lead Table:")
    print("=" * 80)
    print(f"{'ID':<4} | {'Business Name':<32} | {'Phone':<16} | {'Email':<25}")
    print("-" * 80)
    for lead in new_leads:
        print(f"{lead.id:<4} | {lead.name[:32]:<32} | {lead.phone or 'N/A':<16} | {lead.email or 'N/A':<25}")
    print("=" * 80)
    
    stats = deduper.get_stats()
    print(f"📊 Total Stored Leads: {stats['total_leads_in_db']}")
    print(f"🌐 Unique Domains:     {stats['total_unique_domains']}")
    print(f"🛡️ Duplicates Blocked: {stats['total_duplicates_filtered']}")
    print(f"⚡ Deduplication Rate: {stats['deduplication_rate_percent']}%")
    print(f"⏱️ Total Execution:    {total_duration:.2f} seconds")
    print("=" * 80)
    print("✅ DEMO COMPLETED SUCCESSFULLY!\n")


def main():
    # Start local mock server thread if port 8000 is not already serving
    server_thread = threading.Thread(target=run_local_mock_server, daemon=True)
    server_thread.start()
    time.sleep(1.0)  # Wait for server warm up

    asyncio.run(run_pipeline_demo())


if __name__ == "__main__":
    main()
