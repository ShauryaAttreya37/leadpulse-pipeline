#!/usr/bin/env bash
# ==============================================================================
# LeadPulse Webhook Trigger & API Helper Script
# ==============================================================================
BASE_URL="http://localhost:8000"

echo "=== 1. Health Check ==="
curl -s -X GET "$BASE_URL/health" | jq . || curl -s -X GET "$BASE_URL/health"
echo -e "\n"

echo "=== 2. Triggering Autonomous Scraping Webhook (Background Task) ==="
JOB_RESP=$(curl -s -X POST "$BASE_URL/webhook/trigger" \
  -H "Content-Type: application/json" \
  -d '{
    "target": "real_estate",
    "query": "Commercial Real Estate Agencies",
    "location": "Austin, TX",
    "max_pages": 2,
    "enrich_contacts": true,
    "sync_to_sheets": true,
    "notify_slack": true
  }')
echo "$JOB_RESP"
JOB_ID=$(echo "$JOB_RESP" | grep -o '"job_id":"[^"]*' | cut -d'"' -f4)
echo "Triggered Job ID: $JOB_ID"
echo -e "\n"

if [ -n "$JOB_ID" ]; then
  echo "Waiting 6 seconds for scraper to run..."
  sleep 6
  echo "=== 3. Polling Job Status ==="
  curl -s -X GET "$BASE_URL/jobs/$JOB_ID" | jq . || curl -s -X GET "$BASE_URL/jobs/$JOB_ID"
  echo -e "\n"
fi

echo "=== 4. Direct Webhook Ingest (e.g. from Zapier / Form) ==="
curl -s -X POST "$BASE_URL/webhook/ingest" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Hill Country Commercial Brokerage",
    "category": "Commercial Brokerage",
    "domain": "hillcountrybrokerage.com",
    "phone": "(512) 555-7744",
    "email": "partners@hillcountrybrokerage.com",
    "address": "2200 Barton Springs Rd",
    "city": "Austin",
    "state": "TX",
    "zip_code": "78704",
    "website": "https://www.hillcountrybrokerage.com"
  }' | jq . || true
echo -e "\n"

echo "=== 5. Fetching Real-Time Pipeline Stats ==="
curl -s -X GET "$BASE_URL/stats" | jq . || curl -s -X GET "$BASE_URL/stats"
echo -e "\n"
