import httpx
import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any
from app.config import settings
from app.models import LeadRecord

logger = logging.getLogger("LeadPulse.Slack")


class SlackNotifier:
    """
    Slack incoming webhook dispatcher with rich Block Kit layout.
    Sends lead alerts with contact details, ratings, and action buttons.
    """

    def __init__(self):
        self.webhook_url = settings.SLACK_WEBHOOK_URL
        self.log_path = settings.DATA_DIR / "slack_notifications.log"

    def _format_lead_block(self, lead: LeadRecord) -> Dict[str, Any]:
        """Creates an elegant Slack Block Kit payload for a single B2B lead."""
        stars = "⭐" * int(round(lead.rating or 5.0))
        rating_text = f"{stars} {lead.rating} ({lead.review_count} reviews)" if lead.rating else "Not yet rated"
        
        email_display = f"`{lead.email}`" if lead.email else "_Not found_"
        phone_display = f"`{lead.phone}`" if lead.phone else "_Not found_"
        domain_display = f"*{lead.domain}*" if lead.domain else "_No website_"
        location_display = f"{lead.city or ''}, {lead.state or ''}".strip(", ") or "_Location undisclosed_"

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🏢 New Real Estate Lead: {lead.name}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Category:*\n{lead.category}"},
                    {"type": "mrkdwn", "text": f"*Market:*\n📍 {location_display}"},
                    {"type": "mrkdwn", "text": f"*Phone:*\n📞 {phone_display}"},
                    {"type": "mrkdwn", "text": f"*Email:*\n✉️ {email_display}"},
                    {"type": "mrkdwn", "text": f"*Canonical Domain:*\n🌐 {domain_display}"},
                    {"type": "mrkdwn", "text": f"*Reputation:*\n{rating_text}"}
                ]
            }
        ]

        # Add interactive action buttons if links exist
        elements = []
        if lead.website:
            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "🌐 Visit Website", "emoji": True},
                "url": lead.website,
                "style": "primary"
            })
        if lead.social_linkedin:
            elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "💼 LinkedIn", "emoji": True},
                "url": lead.social_linkedin
            })

        if elements:
            blocks.append({
                "type": "actions",
                "elements": elements
            })

        blocks.append({"type": "divider"})
        return {"blocks": blocks}

    def _format_summary_block(
        self,
        query: str,
        location: str,
        total_found: int,
        new_leads: int,
        duplicates: int,
        duration: float
    ) -> Dict[str, Any]:
        """Creates a scrape completion summary card for Slack."""
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🚀 LeadPulse Scraping Pipeline Finished",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Target Query:* `{query}` in *{location}*\n"
                        f"• *Total Discovered:* `{total_found}` agencies\n"
                        f"• *✨ New Clean Leads Pushed:* `{new_leads}`\n"
                        f"• *♻️ Duplicate Listings Caught:* `{duplicates}` (saved CRM quota)\n"
                        f"• *Execution Time:* `{duration:.1f}s`"
                    )
                }
            },
            {"type": "divider"}
        ]
        return {"blocks": blocks}

    async def send_lead_alert(self, lead: LeadRecord) -> bool:
        """Sends a single lead notification to Slack."""
        payload = self._format_lead_block(lead)
        return await self._dispatch_slack_payload(payload, description=f"Lead alert: {lead.name}")

    async def send_summary(
        self,
        query: str,
        location: str,
        total_found: int,
        new_leads: int,
        duplicates: int,
        duration: float
    ) -> bool:
        """Dispatches run summary to Slack."""
        payload = self._format_summary_block(query, location, total_found, new_leads, duplicates, duration)
        return await self._dispatch_slack_payload(payload, description="Pipeline Summary")

    async def _dispatch_slack_payload(self, payload: Dict[str, Any], description: str) -> bool:
        """Dispatches JSON payload to webhook or logs to simulation audit log."""
        timestamp = datetime.now(timezone.utc).isoformat()

        # If live webhook is configured
        if self.webhook_url and self.webhook_url.startswith("http"):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(self.webhook_url, json=payload)
                    if resp.status_code in (200, 204):
                        logger.info(f"Slack webhook delivered successfully: {description}")
                        return True
                    else:
                        logger.warning(f"Slack webhook returned status {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.error(f"Failed to post to Slack webhook: {e}")

        # Safe simulation mode: log payload to file and console
        entry = {
            "timestamp": timestamp,
            "description": description,
            "payload": payload
        }
        try:
            with open(self.log_path, mode="a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Error writing to Slack log fallback: {e}")

        logger.info(f"[Slack Fallback] Dispatched {description} (Logged to {self.log_path.name})")
        return True
