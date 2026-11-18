import base64
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, Optional
import json
from datetime import datetime

from config.settings import settings
from src.core.logger import logger


class GmailClient:
    """Client for Google Gmail API to save personalized cold outreach emails

    directly to the user's Gmail Drafts folder.
    """

    SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

    def __init__(
        self,
        user_email: Optional[str] = None,
        client_secret_file: Optional[str] = None,
        token_file: Optional[str] = None,
    ):
        self.user_email = user_email or settings.GMAIL_USER_EMAIL or "me"
        self.client_secret_file = client_secret_file or settings.GMAIL_CLIENT_SECRET_FILE
        self.token_file = token_file or str(settings.PROJECT_ROOT / "config" / "gmail_token.json")
        self.drafts_dir = settings.DATA_DIR / "drafts"
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self._service = None
        self._init_service()

    def _init_service(self) -> None:
        """Authenticate with Gmail API using OAuth2 credentials."""
        token_path = Path(self.token_file)
        secret_path = Path(self.client_secret_file) if self.client_secret_file else None

        if not token_path.exists() and (not secret_path or not secret_path.exists()):
            logger.warning(
                f"Gmail OAuth credentials not found at [{self.client_secret_file}]. "
                f"Operating in Local Drafts mode (saving to {self.drafts_dir})."
            )
            return

        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            creds = None
            if token_path.exists():
                creds = Credentials.from_authorized_user_file(str(token_path), self.SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                elif secret_path and secret_path.exists():
                    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), self.SCOPES)
                    creds = flow.run_local_server(port=0)

                if creds:
                    token_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(token_path, "w", encoding="utf-8") as token:
                        token.write(creds.to_json())

            if creds:
                self._service = build("gmail", "v1", credentials=creds)
                logger.info(f"Successfully authenticated Gmail API for user: {self.user_email}")

        except Exception as e:
            logger.error(f"Failed to initialize Gmail API service: {e}. Fallback to local draft file.")
            self._service = None

    def create_draft(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a draft email in Gmail. Falls back to saving a local .eml and .json draft."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_to = to_email.replace("@", "_at_").replace(".", "_")

        # Construct standard MIME Email
        msg = EmailMessage()
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["From"] = settings.GMAIL_USER_EMAIL or "me"
        msg.set_content(body_text)

        if body_html:
            msg.add_alternative(body_html, subtype="html")

        # 1. Save directly to Gmail API Drafts if authenticated
        if self._service:
            try:
                raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
                draft_body = {"message": {"raw": raw_message}}
                result = (
                    self._service.users()
                    .drafts()
                    .create(userId="me", body=draft_body)
                    .execute()
                )
                draft_id = result.get("id")
                logger.info(f"Created Gmail Draft successfully (ID: {draft_id}) for [{to_email}].")
                return {
                    "status": "gmail_draft_created",
                    "draft_id": draft_id,
                    "to": to_email,
                    "subject": subject,
                }
            except Exception as e:
                logger.error(f"Failed to create Gmail draft via API: {e}. Falling back to local file.")

        # 2. Local Draft Fallback (.eml and .json metadata)
        eml_path = self.drafts_dir / f"draft_{safe_to}_{timestamp}.eml"
        with open(eml_path, "wb") as f:
            f.write(msg.as_bytes())

        meta_path = self.drafts_dir / f"draft_{safe_to}_{timestamp}.json"
        meta_data = {
            "to": to_email,
            "subject": subject,
            "body": body_text,
            "created_at": datetime.now().isoformat(),
            "status": "saved_locally",
            "eml_file": str(eml_path),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, indent=2)

        logger.info(f"Saved local draft fallback to: {eml_path}")
        return {
            "status": "local_draft_saved",
            "eml_path": str(eml_path),
            "meta_path": str(meta_path),
            "to": to_email,
            "subject": subject,
        }
