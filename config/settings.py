import os
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Base Paths
    PROJECT_ROOT: Path = BASE_DIR
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = BASE_DIR / "data"
    SESSIONS_DIR: Path = BASE_DIR / "sessions"
    LOGS_DIR: Path = BASE_DIR / "logs"
    SCREENSHOTS_DIR: Path = BASE_DIR / "logs" / "screenshots"
    REPORTS_DIR: Path = BASE_DIR / "logs" / "reports"
    GENERATED_RESUMES_DIR: Path = BASE_DIR / "data" / "generated_resumes"
    CRM_LOCAL_BACKUP_CSV: Path = BASE_DIR / "data" / "leads_crm_backup.csv"

    # Naukri Credentials
    NAUKRI_USERNAME: Optional[str] = None
    NAUKRI_PASSWORD: Optional[str] = None

    # Bayt Credentials
    BAYT_USERNAME: Optional[str] = None
    BAYT_PASSWORD: Optional[str] = None

    # Browser Automation Settings
    HEADLESS: bool = True
    BROWSER_TIMEOUT_MS: int = 45000
    SLOW_MO: int = 50
    STEALTH_MODE: bool = True
    DEFAULT_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    # Timezones & Scheduling
    DEFAULT_TIMEZONE: str = "Asia/Kolkata"
    GULF_TIMEZONE: str = "Asia/Dubai"
    GULF_CRON_ENABLED: bool = True
    GULF_WINDOW_START_HOUR: int = 10
    GULF_WINDOW_END_HOUR: int = 13

    # Lead Generation (Module 2)
    APOLLO_API_KEY: Optional[str] = None
    HUNTER_API_KEY: Optional[str] = None
    TARGET_HUBS: List[str] = ["Pune, India", "Dubai, UAE"]
    TARGET_AGENCY_MIN_EMPLOYEES: int = 10
    TARGET_AGENCY_MAX_EMPLOYEES: int = 50
    TARGET_TITLES: List[str] = [
        "Chief Technology Officer",
        "CTO",
        "Founder",
        "Co-Founder",
        "VP of Engineering",
        "VP Engineering",
        "Head of AI",
        "Lead AI Engineer",
        "Director of Engineering",
        "Engineering Manager"
    ]

    # Google Workspace CRM & Gmail
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None
    GOOGLE_SHEETS_SPREADSHEET_ID: Optional[str] = None
    GOOGLE_SHEETS_RANGE: str = "Leads!A:I"
    GMAIL_USER_EMAIL: Optional[str] = None
    GMAIL_APP_PASSWORD: Optional[str] = None
    GMAIL_CLIENT_SECRET_FILE: Optional[str] = None

    # LLM Settings (Tiered Fallback: Ollama -> Groq -> Gemini)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3:latest"
    OLLAMA_TEMPERATURE: float = 0.2

    # Cloud LLM Fallbacks
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "openai/gpt-oss-120b"

    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-3.8-flash"

    # ChromaDB (Vector DB for JD chunking & RAG)
    CHROMA_PERSIST_DIR: Path = BASE_DIR / "data" / "chroma_db"

    # Fallback Alerts
    FALLBACK_WEBHOOK_URL: Optional[str] = None

    def ensure_directories(self) -> None:
        """Create essential directories if they do not exist."""
        self.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        self.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        self.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (self.DATA_DIR / "templates").mkdir(parents=True, exist_ok=True)
        self.GENERATED_RESUMES_DIR.mkdir(parents=True, exist_ok=True)
        self.CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_directories()
