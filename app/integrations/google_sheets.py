import csv
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from app.config import settings
from app.models import LeadRecord

logger = logging.getLogger("LeadPulse.GoogleSheets")

HEADERS = [
    "Lead ID",
    "Business Name",
    "Category",
    "Canonical Domain",
    "Phone",
    "Email",
    "Street Address",
    "City",
    "State",
    "Zip Code",
    "Star Rating",
    "Reviews",
    "Website URL",
    "LinkedIn Profile",
    "Discovered At (UTC)",
    "Times Seen"
]


class GoogleSheetsClient:
    """
    Google Sheets API integration for pushing clean, deduplicated B2B leads.
    Includes automatic header creation, formatting, batch appending,
    and a local CSV live mirror fallback.
    """

    def __init__(self):
        self.creds_file = settings.GOOGLE_SERVICE_ACCOUNT_FILE
        self.sheet_id = settings.GOOGLE_SHEET_ID
        self.sheet_title = settings.GOOGLE_SHEET_TITLE
        self.mirror_csv_path = settings.DATA_DIR / "google_sheets_live_mirror.csv"
        self._client = None
        self._worksheet = None
        self._init_csv_mirror()

    def _init_csv_mirror(self):
        """Initializes the local CSV fallback file with headers if it doesn't exist."""
        if not self.mirror_csv_path.exists():
            with open(self.mirror_csv_path, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(HEADERS)

    def _get_gspread_worksheet(self):
        """Attempts to authenticate with Google Sheets API via gspread."""
        if self._worksheet:
            return self._worksheet

        if not self.creds_file or not Path(self.creds_file).is_file():
            return None

        try:
            import gspread
            self._client = gspread.service_account(filename=self.creds_file)

            if self.sheet_id:
                spreadsheet = self._client.open_by_key(self.sheet_id)
            else:
                try:
                    spreadsheet = self._client.open(self.sheet_title)
                except gspread.SpreadsheetNotFound:
                    logger.info(f"Creating new Google Spreadsheet: '{self.sheet_title}'")
                    spreadsheet = self._client.create(self.sheet_title)

            self._worksheet = spreadsheet.sheet1
            
            # Ensure headers exist in the remote sheet
            existing_rows = self._worksheet.row_values(1)
            if not existing_rows:
                logger.info("Initializing header row in Google Sheet...")
                self._worksheet.append_row(HEADERS)
                # Freeze top row
                self._worksheet.freeze(rows=1)

            return self._worksheet
        except Exception as e:
            logger.warning(f"Google Sheets API connection failed: {e}. Falling back to local mirror.")
            return None

    def _lead_to_row(self, lead: LeadRecord) -> List[Any]:
        return [
            lead.id,
            lead.name,
            lead.category,
            lead.domain or "",
            lead.phone or "",
            lead.email or "",
            lead.address or "",
            lead.city or "",
            lead.state or "",
            lead.zip_code or "",
            lead.rating if lead.rating is not None else "",
            lead.review_count or 0,
            lead.website or "",
            lead.social_linkedin or "",
            lead.first_seen_at,
            lead.times_seen
        ]

    async def push_leads(self, leads: List[LeadRecord]) -> Dict[str, Any]:
        """
        Pushes a list of clean, deduplicated leads to Google Sheets (and local mirror).
        Returns a status dictionary with count and destination.
        """
        if not leads:
            return {"status": "skipped", "count": 0, "destination": "none"}

        rows = [self._lead_to_row(lead) for lead in leads]

        # 1. Always mirror to local CSV for 100% offline audit trail and instant verification
        try:
            with open(self.mirror_csv_path, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerows(rows)
        except Exception as e:
            logger.error(f"Error writing to local CSV mirror: {e}")

        # 2. Attempt Google Sheets Cloud Sync
        worksheet = self._get_gspread_worksheet()
        if worksheet:
            try:
                worksheet.append_rows(rows, value_input_option="USER_ENTERED")
                logger.info(f"Successfully pushed {len(rows)} leads to Google Sheets ('{self.sheet_title}')")
                return {
                    "status": "synced",
                    "count": len(rows),
                    "destination": f"Google Sheets ({self.sheet_title})",
                    "mirror_file": str(self.mirror_csv_path)
                }
            except Exception as e:
                logger.error(f"Failed to append rows to Google Sheets: {e}")

        # 3. Safe fallback reporting
        logger.info(
            f"[Google Sheets Fallback] {len(rows)} clean leads written to '{self.mirror_csv_path.name}'. "
            "To sync live to Google Drive, specify GOOGLE_SERVICE_ACCOUNT_FILE in .env"
        )
        return {
            "status": "mirrored_locally",
            "count": len(rows),
            "destination": f"Local Mirror ({self.mirror_csv_path})",
            "message": "Ready for Google Cloud. Synced to local mirror file."
        }
