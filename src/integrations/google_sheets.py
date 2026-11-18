import csv
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel

from config.settings import settings
from src.core.logger import logger
from src.integrations.apollo_client import LeadContact

CRM_HEADERS = [
    "Company Name",
    "Domain",
    "Contact Name",
    "Title",
    "Email",
    "LinkedIn",
    "Location",
    "Status",
    "Discovered Date",
    "Notes",
]


class GoogleSheetsCRM:
    """Manages reading target domains and synchronizing extracted leadership leads

    with Google Sheets CRM, with automatic local CSV backup.
    """

    def __init__(
        self,
        spreadsheet_id: Optional[str] = None,
        credentials_file: Optional[str] = None,
    ):
        self.spreadsheet_id = spreadsheet_id or settings.GOOGLE_SHEETS_SPREADSHEET_ID
        self.credentials_file = (
            credentials_file or settings.GOOGLE_APPLICATION_CREDENTIALS
        )
        self.backup_csv: Path = settings.CRM_LOCAL_BACKUP_CSV
        self._service = None
        self._init_service()

    def _init_service(self) -> None:
        """Initialize Google Sheets API client if service account exists."""
        if not self.credentials_file or not Path(self.credentials_file).exists():
            logger.warning(
                f"Google application credentials not found at [{self.credentials_file}]. "
                f"Operating in Local CSV Backup mode ({self.backup_csv})."
            )
            return

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_file(
                self.credentials_file,
                scopes=["https://www.googleapis.com/auth/spreadsheets"],
            )
            self._service = build("sheets", "v4", credentials=creds)
            logger.info("Successfully authenticated with Google Sheets API.")
        except Exception as e:
            logger.error(f"Failed to authenticate with Google Sheets API: {e}. Fallback enabled.")
            self._service = None

    def read_target_domains(self) -> List[str]:
        """Read target domains to scrape from Google Sheet (column B) or local backup CSV."""
        domains: List[str] = []

        if self._service and self.spreadsheet_id:
            try:
                sheet = self._service.spreadsheets()
                result = (
                    sheet.values()
                    .get(spreadsheetId=self.spreadsheet_id, range="Targets!A2:B")
                    .execute()
                )
                rows = result.get("values", [])
                for row in rows:
                    if len(row) >= 2 and row[1]:
                        domains.append(row[1].strip().lower())
                    elif len(row) >= 1 and "." in row[0]:
                        domains.append(row[0].strip().lower())
                logger.info(f"Loaded {len(domains)} target domains from Google Sheet.")
                return list(set(domains))
            except Exception as e:
                logger.warning(f"Failed to read from Google Sheet: {e}. Checking local backup.")

        # Read from local backup if available
        if self.backup_csv.exists():
            try:
                with open(self.backup_csv, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        domain = r.get("Domain")
                        if domain:
                            domains.append(domain.strip().lower())
            except Exception as e:
                logger.error(f"Error reading local backup CSV: {e}")

        # Default sample boutique agencies in Dubai & Pune if no input domains found
        if not domains:
            domains = [
                "aiq.ae",
                "derq.com",
                "amplifience.ai",
                "presight.ai",
                "verloop.io",
                "quantiphi.com",
            ]
            logger.info(f"Using default curated Dubai & Pune GenAI target agencies: {domains}")

        return list(set(domains))

    def append_leads(self, leads: List[LeadContact]) -> int:
        """Sync discovered leadership leads to Google Sheets and local CSV backup."""
        if not leads:
            logger.info("No new leads to append.")
            return 0

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows_to_insert = []
        for lead in leads:
            rows_to_insert.append([
                lead.company_name,
                lead.company_domain,
                lead.full_name,
                lead.title,
                lead.email or "N/A",
                lead.linkedin_url or "N/A",
                lead.location,
                "New Lead",
                now_str,
                lead.notes or f"Discovered via {lead.source}",
            ])

        # 1. Write to Google Sheet if authenticated
        synced_to_cloud = False
        if self._service and self.spreadsheet_id:
            try:
                body = {"values": rows_to_insert}
                self._service.spreadsheets().values().append(
                    spreadsheetId=self.spreadsheet_id,
                    range=settings.GOOGLE_SHEETS_RANGE,
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body=body,
                ).execute()
                logger.info(f"Appended {len(rows_to_insert)} leads to Google Sheets CRM.")
                synced_to_cloud = True
            except Exception as e:
                logger.error(f"Failed appending to Google Sheets: {e}")

        # 2. Always persist to Local CSV Backup
        self._write_local_csv(rows_to_insert)

        return len(rows_to_insert)

    def _write_local_csv(self, rows: List[List[str]]) -> None:
        """Write leads to local CSV backup file with deduplication."""
        self.backup_csv.parent.mkdir(parents=True, exist_ok=True)
        file_exists = self.backup_csv.exists()

        existing_emails = set()
        if file_exists:
            try:
                with open(self.backup_csv, "r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    next(reader, None)  # header
                    for r in reader:
                        if len(r) >= 5 and r[4] != "N/A":
                            existing_emails.add(r[4].lower())
            except Exception:
                pass

        new_unique_rows = []
        for r in rows:
            email = r[4].lower()
            if email == "n/a" or email not in existing_emails:
                new_unique_rows.append(r)
                if email != "n/a":
                    existing_emails.add(email)

        if not new_unique_rows:
            logger.info("All leads already exist in local CRM backup.")
            return

        with open(self.backup_csv, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(CRM_HEADERS)
            writer.writerows(new_unique_rows)

        logger.info(f"Saved {len(new_unique_rows)} unique leads to local CRM backup: {self.backup_csv}")
