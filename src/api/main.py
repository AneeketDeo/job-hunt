import asyncio
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import pytz
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Enforce Windows Proactor Event Loop Policy for Playwright subprocesses
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from config.settings import settings
from src.core.logger import logger
from src.core.llm_router import LLMRouter
from src.core.env_manager import update_env_file
from src.core.profile_repository import ProfileRepository
from src.automation.browser_factory import safe_playwright_exec, BrowserFactory
from src.automation.sso_manager import SSOManager
from src.automation.freshness_cron import (
    AlgorithmicFreshnessCron,
    bootstrap_interactive_session,
)
from src.agents.lead_generator.crm_agent import LeadGenerationCRMAgent
from src.agents.resume_tailor.tailor_graph import DynamicResumeTailorAgent
from src.agents.outreach_drafter.outreach_graph import ContextAwareOutreachAgent
from src.automation.auto_apply_orchestrator import AutoApplyOrchestrator
from src.automation.naukri_apply_engine import NaukriAutoApplyEngine
from src.automation.naukri_scraper import NaukriJobScraper
from src.automation.portal_apply_engine import MultiPortalApplyEngine
from src.automation.pending_questions_manager import PendingQuestionsManager
from src.automation.hourly_apply_cron import hourly_cron_service
from src.agents.simulator.negotiation_engine import NegotiationSimulatorEngine

app = FastAPI(
    title="Job Hunt Orchestrator API",
    description="Command Center Backend for Autonomous Job Search in GenAI across Pune & Dubai",
    version="1.0.0",
)

# Comprehensive HTTP Request & Response Logging Middleware for All Routes
@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    start_time = time.perf_counter()
    client_ip = request.client.host if request.client else "unknown"
    method = request.method
    url_path = request.url.path
    query_str = str(request.query_params)

    # Log incoming request (skip static asset poll noise to keep logs focused)
    is_api = url_path.startswith("/api") or url_path == "/"
    if is_api:
        logger.info(f"--> [{method}] {url_path} | Client: {client_ip} | Query: {query_str or 'None'}")

    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000
        if is_api:
            logger.info(f"<-- [{method}] {url_path} | Status: {response.status_code} | Duration: {duration_ms:.2f}ms")
        return response
    except Exception as exc:
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.error(f"<xx [{method}] {url_path} | FAILED with error: {exc} | Duration: {duration_ms:.2f}ms", exc_info=True)
        raise exc

# Mount Static Directories for Resumes, Screenshots, Reports & Logs
settings.ensure_directories()
app.mount("/resumes", StaticFiles(directory=str(settings.GENERATED_RESUMES_DIR)), name="resumes")
app.mount("/screenshots", StaticFiles(directory=str(settings.SCREENSHOTS_DIR)), name="screenshots")
app.mount("/reports", StaticFiles(directory=str(settings.REPORTS_DIR)), name="reports")
app.mount("/logs", StaticFiles(directory=str(settings.LOGS_DIR)), name="logs")


# =============================================================================
# REQUEST SCHEMAS
# =============================================================================
class FreshnessRequest(BaseModel):
    platform: str = "naukri"
    force: bool = True


class LeadSearchRequest(BaseModel):
    domain: str = "derq.com"
    location: Optional[str] = "Dubai, UAE"


class TailorResumeRequest(BaseModel):
    jd: str
    company: Optional[str] = "Target Company"
    location: Optional[str] = "Dubai, UAE"


class OutreachRequest(BaseModel):
    name: str = "Tariq Al-Mansoor"
    email: str = "tariq@derq.com"
    company: str = "Derq AI"
    location: str = "Dubai, UAE"
    jd: str = ""


class AutoApplyRequest(BaseModel):
    url: str
    location: str = "Dubai, UAE"
    dry_run: bool = True


class NaukriApplyRequest(BaseModel):
    job_url: str
    location: str = "Pune, India"
    dry_run: bool = True
    excluded_companies: Optional[List[str]] = None


class ScrapeAndApplyRequest(BaseModel):
    limit: int = 5
    max_age_days: int = 20
    location: str = "Pune, India"
    dry_run: bool = True
    excluded_companies: Optional[List[str]] = None
    portals: Optional[List[str]] = None


class ChatMessageRequest(BaseModel):
    message: str
    scenario: str = "pune_gcc"


class ResolveQuestionRequest(BaseModel):
    job_id: str
    question: str
    answer: str
    dry_run: bool = True


class ApiSettingsRequest(BaseModel):
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    groq_api_key: Optional[str] = None
    groq_model: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    apollo_api_key: Optional[str] = None
    hunter_api_key: Optional[str] = None
    google_sheets_id: Optional[str] = None
    gmail_email: Optional[str] = None
    gmail_app_password: Optional[str] = None
    webhook_url: Optional[str] = None


class PortalSettingsRequest(BaseModel):
    naukri_username: Optional[str] = None
    naukri_password: Optional[str] = None
    bayt_username: Optional[str] = None
    bayt_password: Optional[str] = None
    gulf_window_start: Optional[int] = 10
    gulf_window_end: Optional[int] = 13
    current_offer_lpa: Optional[float] = 14.0
    india_target_min: Optional[float] = 18.0
    india_target_ideal: Optional[float] = 21.0
    uae_target_min: Optional[int] = 18000
    uae_target_ideal: Optional[int] = 22000
    uae_relocation_status: Optional[str] = "Non-Resident / Willing to relocate - Immediately"


class BootstrapLoginRequest(BaseModel):
    platform: str = "naukri"
    timeout_seconds: int = 300


class SSOBootstrapRequest(BaseModel):
    platform: str = "naukri"
    timeout_seconds: int = 300


class SSOVerifyRequest(BaseModel):
    platform: str = "naukri"


class SSOImportRequest(BaseModel):
    platform: str = "naukri"
    raw_data: str


# =============================================================================
# ROUTES
# =============================================================================
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the single-page Command Center Dashboard."""
    template_path = Path(__file__).parent / "templates" / "index.html"
    if template_path.exists():
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Job Hunt Orchestrator Dashboard</h1>")


@app.get("/api/status")
async def get_system_status():
    """Return live system health, LLM tier status, and Gulf recruiter window."""
    router = LLMRouter()
    gulf_tz = pytz.timezone(settings.GULF_TIMEZONE)
    now_gst = datetime.now(gulf_tz)
    gulf_active = settings.GULF_WINDOW_START_HOUR <= now_gst.hour < settings.GULF_WINDOW_END_HOUR

    active_tier = "Tier 4: Deterministic Heuristic"
    if await router._try_ollama("test"):
        active_tier = f"Tier 1: Local Ollama ({settings.OLLAMA_MODEL})"
    elif settings.GROQ_API_KEY and not settings.GROQ_API_KEY.startswith("gsk_your_"):
        active_tier = f"Tier 2: Groq Cloud ({settings.GROQ_MODEL})"
    elif settings.GEMINI_API_KEY and not settings.GEMINI_API_KEY.startswith("AIzaSy_your_"):
        active_tier = f"Tier 3: Google Gemini ({settings.GEMINI_MODEL})"

    naukri_mgr = SSOManager("naukri")
    bayt_mgr = SSOManager("bayt")
    indeed_mgr = SSOManager("indeed")
    cutshort_mgr = SSOManager("cutshort")
    wellfound_mgr = SSOManager("wellfound")

    return {
        "status": "operational",
        "llm_tier": active_tier,
        "gulf_time_gst": now_gst.strftime("%H:%M:%S GST"),
        "gulf_window_active": gulf_active,
        "playwright_headless": settings.HEADLESS,
        "naukri_authenticated": naukri_mgr.has_session(),
        "naukri_sso": naukri_mgr.get_sso_info(),
        "bayt_authenticated": bayt_mgr.has_session(),
        "bayt_sso": bayt_mgr.get_sso_info(),
        "indeed_authenticated": indeed_mgr.has_session(),
        "indeed_sso": indeed_mgr.get_sso_info(),
        "cutshort_authenticated": cutshort_mgr.has_session(),
        "cutshort_sso": cutshort_mgr.get_sso_info(),
        "wellfound_authenticated": wellfound_mgr.has_session(),
        "wellfound_sso": wellfound_mgr.get_sso_info(),
        "candidate": {
            "title": "Principal GenAI / Agentic Systems Engineer",
            "experience": "3 Years",
            "pune_target": "18 - 21+ LPA (vs 14 LPA baseline)",
            "dubai_target": "AED 18,000 - 22,000 / month (Immediate Relocation)",
        }
    }


# =============================================================================
# ADMIN PANEL 1: ENTERPRISE APIS & LLM CONFIGURATION
# =============================================================================
@app.get("/api/settings/apis")
async def get_api_settings():
    """Get current Enterprise API and LLM configurations."""
    return {
        "ollama_url": settings.OLLAMA_BASE_URL,
        "ollama_model": settings.OLLAMA_MODEL,
        "groq_api_key": settings.GROQ_API_KEY or "",
        "groq_model": settings.GROQ_MODEL,
        "gemini_api_key": settings.GEMINI_API_KEY or "",
        "gemini_model": settings.GEMINI_MODEL,
        "apollo_api_key": settings.APOLLO_API_KEY or "",
        "hunter_api_key": settings.HUNTER_API_KEY or "",
        "google_sheets_id": settings.GOOGLE_SHEETS_SPREADSHEET_ID or "",
        "gmail_email": settings.GMAIL_USER_EMAIL or "",
        "gmail_app_password": settings.GMAIL_APP_PASSWORD or "",
        "webhook_url": settings.FALLBACK_WEBHOOK_URL or "",
    }


@app.post("/api/settings/apis")
async def save_api_settings(req: ApiSettingsRequest):
    """Save Enterprise API and LLM configurations directly to .env."""
    updates = {}
    if req.ollama_url is not None: updates["OLLAMA_BASE_URL"] = req.ollama_url
    if req.ollama_model is not None: updates["OLLAMA_MODEL"] = req.ollama_model
    if req.groq_api_key is not None: updates["GROQ_API_KEY"] = req.groq_api_key
    if req.groq_model is not None: updates["GROQ_MODEL"] = req.groq_model
    if req.gemini_api_key is not None: updates["GEMINI_API_KEY"] = req.gemini_api_key
    if req.gemini_model is not None: updates["GEMINI_MODEL"] = req.gemini_model
    if req.apollo_api_key is not None: updates["APOLLO_API_KEY"] = req.apollo_api_key
    if req.hunter_api_key is not None: updates["HUNTER_API_KEY"] = req.hunter_api_key
    if req.google_sheets_id is not None: updates["GOOGLE_SHEETS_SPREADSHEET_ID"] = req.google_sheets_id
    if req.gmail_email is not None: updates["GMAIL_USER_EMAIL"] = req.gmail_email
    if req.gmail_app_password is not None: updates["GMAIL_APP_PASSWORD"] = req.gmail_app_password
    if req.webhook_url is not None: updates["FALLBACK_WEBHOOK_URL"] = req.webhook_url

    update_env_file(updates)

    # Sync back to settings object
    for k, v in updates.items():
        if hasattr(settings, k):
            setattr(settings, k, v)

    return {"status": "success", "message": "Enterprise API configurations saved successfully to .env."}


# =============================================================================
# ADMIN PANEL 2: JOB PORTAL INTEGRATIONS & RELOCATION SETTINGS
# =============================================================================
@app.get("/api/settings/portals")
async def get_portal_settings():
    """Get current Naukri, Bayt, and compensation settings."""
    naukri_bf = BrowserFactory(platform_name="naukri")
    bayt_bf = BrowserFactory(platform_name="bayt")

    # Load compensation from profile_data.json
    profile_path = settings.DATA_DIR / "profile_data.json"
    comp_data = {}
    personal_data = {}
    if profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as f:
            p_data = json.load(f)
            comp_data = p_data.get("compensation_targets", {})
            personal_data = p_data.get("personal", {})

    naukri_mgr = SSOManager("naukri")
    bayt_mgr = SSOManager("bayt")
    indeed_mgr = SSOManager("indeed")
    cutshort_mgr = SSOManager("cutshort")
    wellfound_mgr = SSOManager("wellfound")

    return {
        "naukri_username": settings.NAUKRI_USERNAME or "",
        "naukri_password": settings.NAUKRI_PASSWORD or "",
        "naukri_has_session": naukri_mgr.has_session(),
        "naukri_sso": naukri_mgr.get_sso_info(),
        "bayt_username": settings.BAYT_USERNAME or "",
        "bayt_password": settings.BAYT_PASSWORD or "",
        "bayt_has_session": bayt_mgr.has_session(),
        "bayt_sso": bayt_mgr.get_sso_info(),
        "indeed_has_session": indeed_mgr.has_session(),
        "indeed_sso": indeed_mgr.get_sso_info(),
        "cutshort_has_session": cutshort_mgr.has_session(),
        "cutshort_sso": cutshort_mgr.get_sso_info(),
        "wellfound_has_session": wellfound_mgr.has_session(),
        "wellfound_sso": wellfound_mgr.get_sso_info(),
        "gulf_window_start": settings.GULF_WINDOW_START_HOUR,
        "gulf_window_end": settings.GULF_WINDOW_END_HOUR,
        "current_offer_lpa": comp_data.get("india", {}).get("current_offer", 14.0),
        "india_target_min": comp_data.get("india", {}).get("target_minimum", 18.0),
        "india_target_ideal": comp_data.get("india", {}).get("target_ideal", 21.0),
        "uae_target_min": comp_data.get("uae", {}).get("target_minimum", 18000),
        "uae_target_ideal": comp_data.get("uae", {}).get("target_ideal", 22000),
        "uae_relocation_status": personal_data.get("uae_relocation_status", "Non-Resident / Willing to relocate - Immediately"),
    }


@app.post("/api/settings/portals")
async def save_portal_settings(req: PortalSettingsRequest):
    """Save portal credentials to .env and update compensation in profile_data.json."""
    env_updates = {}
    if req.naukri_username is not None: env_updates["NAUKRI_USERNAME"] = req.naukri_username
    if req.naukri_password is not None: env_updates["NAUKRI_PASSWORD"] = req.naukri_password
    if req.bayt_username is not None: env_updates["BAYT_USERNAME"] = req.bayt_username
    if req.bayt_password is not None: env_updates["BAYT_PASSWORD"] = req.bayt_password
    if req.gulf_window_start is not None: env_updates["GULF_WINDOW_START_HOUR"] = str(req.gulf_window_start)
    if req.gulf_window_end is not None: env_updates["GULF_WINDOW_END_HOUR"] = str(req.gulf_window_end)

    update_env_file(env_updates)
    for k, v in env_updates.items():
        if hasattr(settings, k):
            setattr(settings, k, int(v) if v.isdigit() else v)

    # Update profile_data.json
    profile_path = settings.DATA_DIR / "profile_data.json"
    if profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = json.load(f)

        profile.setdefault("compensation_targets", {}).setdefault("india", {})["current_offer"] = req.current_offer_lpa
        profile["compensation_targets"]["india"]["target_minimum"] = req.india_target_min
        profile["compensation_targets"]["india"]["target_ideal"] = req.india_target_ideal

        profile.setdefault("compensation_targets", {}).setdefault("uae", {})["target_minimum"] = req.uae_target_min
        profile["compensation_targets"]["uae"]["target_ideal"] = req.uae_target_ideal
        profile.setdefault("personal", {})["uae_relocation_status"] = req.uae_relocation_status

        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2)

    return {"status": "success", "message": "Portal credentials and regional parameters updated successfully."}


# =============================================================================
# CANDIDATE PROFILE REPOSITORY ROUTES
# =============================================================================
@app.get("/api/profile")
async def get_candidate_profile():
    """Retrieve full candidate profile repository."""
    repo = ProfileRepository()
    return repo.load()


@app.post("/api/profile")
async def update_candidate_profile(data: Dict[str, Any]):
    """Update candidate profile repository with resume, summary, links, and Q&A."""
    repo = ProfileRepository()
    repo.save(data)
    return {"status": "success", "message": "Candidate profile repository updated successfully."}


@app.post("/api/profile/upload-resume")
async def upload_candidate_resume(file: UploadFile = File(...)):
    """Upload and activate a custom PDF or DOCX resume."""
    try:
        raw_name = Path(file.filename).name
        # Sanitize filename (replace spaces, preserve extension)
        safe_name = raw_name.replace(" ", "_")
        lower_name = safe_name.lower()

        allowed_exts = (".pdf", ".docx", ".doc")
        if not any(lower_name.endswith(ext) for ext in allowed_exts):
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Supported formats: PDF, DOCX, DOC."}
            )

        settings.GENERATED_RESUMES_DIR.mkdir(parents=True, exist_ok=True)
        save_path = settings.GENERATED_RESUMES_DIR / safe_name
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)

        logger.info(f"Saved uploaded resume ({len(content)} bytes) to: {save_path}")

        # Persistent storage in data/resumes/candidate_resume.pdf
        persistent_dir = settings.DATA_DIR / "resumes"
        persistent_dir.mkdir(parents=True, exist_ok=True)
        persistent_path = persistent_dir / "candidate_resume.pdf"
        with open(persistent_path, "wb") as pf:
            pf.write(content)

        upload_timestamp = datetime.now().isoformat()
        file_size_kb = round(len(content) / 1024, 2)
        resume_metadata = {
            "filename": safe_name,
            "upload_timestamp": upload_timestamp,
            "file_size_kb": file_size_kb,
            "file_path": str(persistent_path),
        }

        repo = ProfileRepository()
        profile = repo.load()
        profile["active_resume_filename"] = safe_name
        profile["resume_file"] = resume_metadata
        repo.save(profile)

        # Also sync to profile_data.json for legacy compatibility
        legacy_path = settings.DATA_DIR / "profile_data.json"
        if legacy_path.exists():
            try:
                with open(legacy_path, "r", encoding="utf-8") as lf:
                    l_data = json.load(lf)
                l_data["active_resume_filename"] = safe_name
                with open(legacy_path, "w", encoding="utf-8") as lf:
                    json.dump(l_data, lf, indent=2)
            except Exception:
                pass

        return {
            "status": "success",
            "filename": safe_name,
            "message": f"Resume '{safe_name}' uploaded and set as active.",
            "url": f"/resumes/{safe_name}",
            "resume_file": resume_metadata,
        }
    except Exception as e:
        logger.exception(f"Error handling resume upload: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.get("/api/profile/resume")
async def get_active_candidate_resume():
    """Download or preview candidate's active persistent resume."""
    repo = ProfileRepository()
    persistent = repo.get_persistent_resume_path()
    if persistent and persistent.exists():
        return FileResponse(
            path=str(persistent),
            filename="candidate_resume.pdf",
            media_type="application/pdf",
        )
    try:
        active_path = repo.get_resume_path()
        if active_path and active_path.exists():
            return FileResponse(
                path=str(active_path),
                filename=active_path.name,
                media_type="application/pdf" if active_path.suffix.lower() == ".pdf" else "application/octet-stream",
            )
    except Exception:
        pass
    return JSONResponse(status_code=404, content={"status": "error", "message": "No active candidate resume found."})


async def _execute_sync_resume_to_naukri() -> Dict[str, Any]:
    engine = NaukriAutoApplyEngine(headless=True)
    return await engine.sync_resume_to_naukri()


@app.post("/api/profile/sync-to-naukri")
async def sync_resume_to_naukri_api():
    """Upload active candidate resume directly to live Naukri profile."""
    try:
        result = await safe_playwright_exec(_execute_sync_resume_to_naukri)
        return result
    except Exception as e:
        logger.error(f"Failed to sync resume to Naukri profile: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


# =============================================================================
# NAUKRI MULTI-PATH AUTO-APPLY ROUTES
# =============================================================================
async def _execute_naukri_apply(
    job_url: str,
    location: str,
    dry_run: bool,
    excluded_companies: Optional[List[str]] = None
) -> Dict[str, Any]:
    engine = NaukriAutoApplyEngine(headless=True, dry_run=dry_run)
    result = await engine.apply_to_job(
        job_url=job_url,
        location=location,
        dry_run=dry_run,
        excluded_companies=excluded_companies
    )
    proof_p = result.get("proof_screenshot")
    if proof_p:
        result["proof_screenshot"] = f"/logs/{Path(proof_p).name}?t={datetime.now().timestamp()}"
    return result


@app.post("/api/naukri/auto-apply")
async def naukri_auto_apply_api(req: NaukriApplyRequest):
    """Execute 3-Path Naukri Auto-Apply Engine (Easy Apply, In-App Questionnaire, External ATS)."""
    logger.info(
        f"API Route: POST /api/naukri/auto-apply | URL: {req.job_url} | Location: {req.location} | "
        f"DryRun: {req.dry_run} | Excluded: {req.excluded_companies}"
    )
    try:
        result = await safe_playwright_exec(
            _execute_naukri_apply,
            req.job_url,
            req.location,
            req.dry_run,
            req.excluded_companies,
        )
        return result
    except Exception as e:
        logger.error(f"Naukri auto-apply failed: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


async def _execute_scrape_and_apply(
    limit: int,
    max_age: int,
    location: str,
    dry_run: bool,
    excluded_companies: Optional[List[str]] = None,
    portals: Optional[List[str]] = None,
) -> Dict[str, Any]:
    # Default to naukri only if no portals specified
    if not portals:
        portals = ["naukri"]
    selected = [p.strip().lower() for p in portals if p.strip()]
    if not selected:
        selected = ["naukri"]

    all_results = {}
    total_scraped = 0
    total_skipped = 0
    total_excluded = 0
    total_applied = 0
    all_executions = []

    # 1. Naukri
    if "naukri" in selected:
        scraper = NaukriJobScraper(headless=True)
        res = await scraper.run_autonomous_apply_cycle(
            max_jobs_to_apply=limit,
            max_age_days=max_age,
            dry_run=dry_run,
            target_location=location,
            excluded_companies=excluded_companies,
        )
        all_results["naukri"] = res
        total_scraped += res.get("jobs_scraped", 0)
        total_skipped += res.get("already_applied_skipped", 0)
        total_excluded += res.get("excluded_companies_skipped", 0)
        total_applied += res.get("jobs_targeted", 0)
        all_executions.extend(res.get("applications_executed", []))

    # 2. Indeed
    if "indeed" in selected:
        try:
            from src.automation.indeed_scraper import IndeedJobScraper
            ind_scraper = IndeedJobScraper(headless=True)
            res = await ind_scraper.run_autonomous_apply_cycle(
                max_jobs_to_apply=limit,
                max_age_days=max_age,
                dry_run=dry_run,
                target_location=location,
                excluded_companies=excluded_companies,
            )
            all_results["indeed"] = res
            total_scraped += res.get("jobs_scraped", 0)
            total_skipped += res.get("already_applied_skipped", 0)
            total_excluded += res.get("excluded_companies_skipped", 0)
            total_applied += res.get("jobs_targeted", 0)
            all_executions.extend(res.get("applications_executed", []))
        except Exception as e:
            logger.error(f"[API] Indeed apply cycle error: {e}")
            all_results["indeed"] = {"status": "error", "message": str(e)}

    # 3. Cutshort
    if "cutshort" in selected:
        try:
            from src.automation.cutshort_scraper import CutshortJobScraper
            cs_scraper = CutshortJobScraper(headless=True)
            res = await cs_scraper.run_autonomous_apply_cycle(
                max_jobs_to_apply=limit,
                max_age_days=max_age,
                dry_run=dry_run,
                target_location=location,
                excluded_companies=excluded_companies,
            )
            all_results["cutshort"] = res
            total_scraped += res.get("jobs_scraped", 0)
            total_skipped += res.get("already_applied_skipped", 0)
            total_excluded += res.get("excluded_companies_skipped", 0)
            total_applied += res.get("jobs_targeted", 0)
            all_executions.extend(res.get("applications_executed", []))
        except Exception as e:
            logger.error(f"[API] Cutshort apply cycle error: {e}")
            all_results["cutshort"] = {"status": "error", "message": str(e)}

    # 4. Wellfound
    if "wellfound" in selected:
        try:
            from src.automation.wellfound_scraper import WellfoundJobScraper
            wf_scraper = WellfoundJobScraper(headless=True)
            res = await wf_scraper.run_autonomous_apply_cycle(
                max_jobs_to_apply=limit,
                max_age_days=max_age,
                dry_run=dry_run,
                target_location=location,
                excluded_companies=excluded_companies,
            )
            all_results["wellfound"] = res
            total_scraped += res.get("jobs_scraped", 0)
            total_skipped += res.get("already_applied_skipped", 0)
            total_excluded += res.get("excluded_companies_skipped", 0)
            total_applied += res.get("jobs_targeted", 0)
            all_executions.extend(res.get("applications_executed", []))
        except Exception as e:
            logger.error(f"[API] Wellfound apply cycle error: {e}")
            all_results["wellfound"] = {"status": "error", "message": str(e)}

    return {
        "status": "cycle_completed",
        "portals_run": selected,
        "jobs_scraped": total_scraped,
        "already_applied_skipped": total_skipped,
        "excluded_companies_skipped": total_excluded,
        "jobs_targeted": total_applied,
        "applications_executed": all_executions,
        "portal_results": all_results,
        "dry_run": dry_run,
    }


@app.post("/api/naukri/scrape-and-apply")
@app.post("/api/multi-portal/scrape-and-apply")
async def scrape_and_apply_api(req: ScrapeAndApplyRequest):
    """Scrape selected job portals for fresh GenAI jobs and auto-apply autonomously."""
    active_portals = req.portals or ["naukri"]
    logger.info(
        f"API Route: POST /api/scrape-and-apply | Portals: {active_portals} | Limit: {req.limit} | "
        f"Location: {req.location} | DryRun: {req.dry_run} | ExcludedCompanies: {req.excluded_companies}"
    )
    try:
        result = await safe_playwright_exec(
            _execute_scrape_and_apply,
            req.limit,
            req.max_age_days,
            req.location,
            req.dry_run,
            req.excluded_companies,
            active_portals,
        )
        return result
    except Exception as e:
        logger.error(f"Autonomous multi-portal scrape and apply failed: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.get("/api/naukri/applied-jobs")
@app.get("/api/jobs/applied")
@app.get("/api/applied-jobs")
async def get_applied_jobs_history():
    """Retrieve history of applied jobs from local ledger."""
    logger.info("API Route: GET /api/naukri/applied-jobs | Fetching ledger history")
    scraper = NaukriJobScraper()
    ledger = scraper.load_applied_ledger()
    jobs_list = sorted(ledger.values(), key=lambda x: x.get("applied_at", ""), reverse=True)
    return {
        "count": len(jobs_list),
        "jobs": jobs_list,
    }


class DeleteJobsRequest(BaseModel):
    job_ids: List[str]


def _remove_jobs_from_ledger_files(job_ids_to_delete: List[str]) -> Tuple[int, List[str], int]:
    """Remove given job IDs from data/applied_jobs.json and backup ledgers."""
    primary_file = settings.DATA_DIR / "applied_jobs.json"
    backup_file = settings.DATA_DIR / "indeed_applied_jobs.json"

    deleted_ids = []
    current_ledger = {}

    if primary_file.exists():
        try:
            with open(primary_file, "r", encoding="utf-8") as f:
                current_ledger = json.load(f)
        except Exception as e:
            logger.error(f"Error loading ledger for deletion: {e}")
            current_ledger = {}

    for jid in job_ids_to_delete:
        if jid in current_ledger:
            del current_ledger[jid]
            deleted_ids.append(jid)

    try:
        with open(primary_file, "w", encoding="utf-8") as f:
            json.dump(current_ledger, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving updated ledger after deletion: {e}")

    # Also clean from backup indeed ledger if present
    if backup_file.exists():
        try:
            with open(backup_file, "r", encoding="utf-8") as f:
                backup_data = json.load(f)
            backup_changed = False
            for jid in job_ids_to_delete:
                if jid in backup_data:
                    del backup_data[jid]
                    backup_changed = True
            if backup_changed:
                with open(backup_file, "w", encoding="utf-8") as f:
                    json.dump(backup_data, f, indent=2)
        except Exception:
            pass

    return len(deleted_ids), deleted_ids, len(current_ledger)


@app.delete("/api/jobs/applied/{job_id}")
@app.delete("/api/applied-jobs/{job_id}")
@app.delete("/api/naukri/applied-jobs/{job_id}")
async def delete_single_applied_job(job_id: str):
    """Delete a single job entry from the applied jobs ledger."""
    logger.info(f"API Route: DELETE /api/jobs/applied/{job_id}")
    count, deleted, remaining = _remove_jobs_from_ledger_files([job_id])
    if count == 0:
        return JSONResponse(status_code=404, content={"status": "not_found", "message": f"Job '{job_id}' not found in ledger."})
    return {
        "status": "success",
        "message": f"Job '{job_id}' deleted successfully from ledger.",
        "deleted_count": count,
        "deleted_ids": deleted,
        "remaining_count": remaining,
    }


@app.post("/api/jobs/applied/delete")
@app.post("/api/applied-jobs/delete")
@app.post("/api/jobs/delete")
async def delete_bulk_applied_jobs(req: DeleteJobsRequest):
    """Delete multiple job entries from the applied jobs ledger."""
    logger.info(f"API Route: POST /api/jobs/applied/delete | Request to delete {len(req.job_ids)} jobs")
    if not req.job_ids:
        return {"status": "success", "message": "No job IDs provided.", "deleted_count": 0, "deleted_ids": [], "remaining_count": 0}
    count, deleted, remaining = _remove_jobs_from_ledger_files(req.job_ids)
    return {
        "status": "success",
        "message": f"Deleted {count} job(s) from ledger.",
        "deleted_count": count,
        "deleted_ids": deleted,
        "remaining_count": remaining,
    }


class RetryFailedRequest(BaseModel):
    dry_run: Optional[bool] = None


async def _execute_retry_failed_jobs(dry_run: bool) -> Dict[str, Any]:
    scraper = NaukriJobScraper()
    ledger = scraper.load_applied_ledger()

    failed_jobs = []
    for jid, entry in list(ledger.items()):
        status = entry.get("status", "").lower()
        if status in ["failed", "error"]:
            failed_jobs.append(entry)

    logger.info(f"[RETRY-FAILED] Located {len(failed_jobs)} failed/error applications in ledger.")

    if not failed_jobs:
        return {
            "status": "completed",
            "message": "No failed applications found in ledger to retry.",
            "retried_count": 0,
            "jobs_retried": [],
        }

    retried_results = []
    for job in failed_jobs:
        job_id = job.get("job_id")
        job_url = job.get("url") or job.get("job_url")
        if not job_id or not job_url:
            continue

        prev_count = job.get("retry_count", 0)
        new_count = prev_count + 1
        source = job.get("source", "").lower()

        # Identify target portal
        if source == "cutshort" or job_id.startswith("cs_") or "cutshort.io" in job_url.lower():
            portal = "cutshort"
        elif source == "indeed" or job_id.startswith("ind_") or "indeed.com" in job_url.lower():
            portal = "indeed"
        elif source == "wellfound" or job_id.startswith("wf_") or "wellfound.com" in job_url.lower():
            portal = "wellfound"
        else:
            portal = "naukri"

        logger.info(f"[RETRY-FAILED] Retrying {job.get('title')} @ {job.get('company')} via {portal.upper()} (attempt {new_count})...")

        try:
            if portal == "naukri":
                engine = NaukriAutoApplyEngine(headless=True, dry_run=dry_run)
                res = await engine.apply_to_job(
                    job_url=job_url,
                    location=job.get("location", "Pune, India"),
                    dry_run=dry_run,
                )
            else:
                engine = MultiPortalApplyEngine(platform=portal, headless=True, dry_run=dry_run)
                res = await engine.apply_to_job(
                    job_url=job_url,
                    location=job.get("location", "Pune, India"),
                    dry_run=dry_run,
                    job_info=job,
                )

            res_status = res.get("status", "applied")
            if res_status in ["failed", "error"]:
                final_status = "failed"
                err_msg = res.get("message") or "Application failed"
            else:
                final_status = "applied"
                err_msg = None

            ledger[job_id] = {
                **job,
                "applied_at": datetime.now().isoformat(),
                "path": res.get("path", job.get("path", "path_3_external_ats")),
                "status": final_status,
                "retry_count": new_count,
                "error_message": err_msg,
                "dry_run": dry_run,
                "proof_screenshot": res.get("proof_screenshot") or job.get("proof_screenshot"),
                "message": res.get("message") or f"Retried via {portal}.",
            }
            scraper.save_applied_ledger(ledger)
            retried_results.append({
                "job_id": job_id,
                "title": job.get("title"),
                "company": job.get("company"),
                "portal": portal,
                "result": res,
                "status": final_status,
            })
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"[RETRY-FAILED] Exception retrying {job.get('title')}: {e}")
            ledger[job_id] = {
                **job,
                "applied_at": datetime.now().isoformat(),
                "status": "failed",
                "retry_count": new_count,
                "error_message": str(e),
                "dry_run": dry_run,
                "message": f"Retry error: {e}",
            }
            scraper.save_applied_ledger(ledger)
            retried_results.append({
                "job_id": job_id,
                "title": job.get("title"),
                "company": job.get("company"),
                "portal": portal,
                "status": "failed",
                "error": str(e),
            })

    return {
        "status": "completed",
        "message": f"Retried {len(retried_results)} failed application(s).",
        "retried_count": len(retried_results),
        "jobs_retried": retried_results,
    }


@app.post("/api/jobs/retry-failed")
async def retry_failed_applications(req: Optional[RetryFailedRequest] = None):
    """Collect all jobs in applied_jobs.json with status in ['failed', 'error'],
    reset them for retry, and execute application via MultiPortalApplyEngine / NaukriAutoApplyEngine."""
    dry_run = req.dry_run if (req and req.dry_run is not None) else False
    logger.info(f"API Route: POST /api/jobs/retry-failed | DryRun: {dry_run}")
    try:
        return await safe_playwright_exec(_execute_retry_failed_jobs, dry_run)
    except Exception as e:
        logger.error(f"Error in retry_failed_applications: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.get("/api/logs/recent")
async def get_recent_logs(lines: int = 60):
    """Retrieve recent server execution log lines."""
    log_file = settings.LOGS_DIR / "orchestrator.log"
    if not log_file.exists():
        return {"lines": []}
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            recent = [line.strip() for line in all_lines[-lines:] if line.strip()]
            return {"count": len(recent), "lines": recent}
    except Exception as e:
        return {"error": str(e), "lines": []}


# =============================================================================
# PENDING SCREENING QUESTIONS & HOURLY CRON ROUTES
# =============================================================================
@app.get("/api/naukri/pending-questions")
async def get_pending_questions_api():
    """Retrieve all unanswered screening questions requiring candidate input."""
    mgr = PendingQuestionsManager()
    pending = mgr.load_pending()
    logger.info(f"API Route: GET /api/naukri/pending-questions | Returning {len(pending)} pending questions")
    
    formatted_questions = []
    for q in pending.values():
        item = dict(q)
        ps = item.get("proof_screenshot")
        if ps:
            fname = Path(ps).name
            if (settings.SCREENSHOTS_DIR / fname).exists():
                item["proof_screenshot_url"] = f"/screenshots/{fname}"
            else:
                item["proof_screenshot_url"] = f"/logs/{fname}"
        formatted_questions.append(item)

    return {
        "count": len(formatted_questions),
        "questions": formatted_questions,
    }


@app.post("/api/naukri/resolve-question")
async def resolve_question_api(req: ResolveQuestionRequest):
    """Save candidate's answer to profile repository and immediately re-trigger auto-apply for that job."""
    logger.info(f"API Route: POST /api/naukri/resolve-question | Job: {req.job_id} | Answer: '{req.answer}'")
    mgr = PendingQuestionsManager()
    result = await mgr.resolve_and_reapply(
        job_id=req.job_id,
        question=req.question,
        answer=req.answer,
        dry_run=req.dry_run,
    )
    return result


@app.get("/api/cron/status")
async def get_cron_status_api():
    """Get the live status of the 1-hour autonomous scraper cron job."""
    return hourly_cron_service.get_status()


@app.post("/api/cron/toggle")
async def toggle_cron_api():
    """Start or stop the 1-hour autonomous background scraper cron."""
    if hourly_cron_service.is_running:
        res = hourly_cron_service.stop()
    else:
        res = hourly_cron_service.start()
    return res


@app.post("/api/cron/run-now")
async def run_cron_now_api(dry_run: bool = False, portals: str = "naukri"):
    """Trigger an immediate autonomous apply run across portals and send report to email."""
    portals_list = [p.strip().lower() for p in portals.split(",") if p.strip()]
    if not portals_list:
        portals_list = ["naukri"]
    logger.info(f"API Route: POST /api/cron/run-now | Executing immediate cycle (dry_run={dry_run}, portals={portals_list})")
    try:
        summary = await hourly_cron_service.run_single_cycle(dry_run=dry_run, portals=portals_list)
        await hourly_cron_service._send_email_report(summary)
        return {
            "status": "success",
            "message": f"Autonomous run completed across {', '.join(portals_list)} and report dispatched to {hourly_cron_service.recipient_email}",
            "summary": summary,
        }
    except Exception as e:
        logger.error(f"Error in manual cron run: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


# =============================================================================
# SSO AUTHENTICATION & SESSION MANAGEMENT ROUTES
# =============================================================================
@app.get("/api/sso/status")
async def get_sso_status_api():
    """Retrieve active SSO authentication status, candidate names, and cookie counts for all platforms."""
    return {
        "naukri": SSOManager("naukri").get_sso_info(),
        "bayt": SSOManager("bayt").get_sso_info(),
        "indeed": SSOManager("indeed").get_sso_info(),
        "cutshort": SSOManager("cutshort").get_sso_info(),
        "wellfound": SSOManager("wellfound").get_sso_info(),
    }


async def _exec_sso_bootstrap(platform: str, timeout: int) -> Dict[str, Any]:
    mgr = SSOManager(platform)
    return await mgr.bootstrap_sso_session(timeout_seconds=timeout)


@app.post("/api/sso/bootstrap")
async def run_sso_bootstrap_api(req: SSOBootstrapRequest):
    """Launch visible Edge/Chrome for SSO authentication (Google/LinkedIn) without bot blocks."""
    logger.info(f"Triggering interactive SSO bootstrap for [{req.platform}] (timeout: {req.timeout_seconds}s)...")
    if sys.platform == "win32":
        import subprocess
        subprocess.Popen(
            [sys.executable, "-m", "src.automation.sso_manager", "--platform", req.platform, "--timeout", str(req.timeout_seconds)],
            cwd=str(settings.PROJECT_ROOT),
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
        )
        return {
            "status": "launched",
            "platform": req.platform,
            "message": f"Interactive Microsoft Edge login window launched on your desktop for {req.platform.upper()}! Please complete sign-in with Google/LinkedIn in the opened window. Cookies and profile will be saved automatically.",
        }
    else:
        result = await safe_playwright_exec(_exec_sso_bootstrap, req.platform, req.timeout_seconds)
        return result


async def _exec_sso_verify(platform: str) -> Dict[str, Any]:
    mgr = SSOManager(platform)
    return await mgr.verify_session()


@app.post("/api/sso/verify")
async def run_sso_verify_api(req: SSOVerifyRequest):
    """Headless verification of saved SSO session state."""
    logger.info(f"Triggering headless SSO session verification for [{req.platform}]...")
    result = await safe_playwright_exec(_exec_sso_verify, req.platform)
    return result


@app.post("/api/sso/import")
async def import_sso_session_api(req: SSOImportRequest):
    """Import raw cookies or Playwright storageState JSON for a portal."""
    mgr = SSOManager(req.platform)
    return mgr.import_session_json(req.raw_data)


@app.post("/api/integrations/bootstrap-login")
async def bootstrap_login_api(req: BootstrapLoginRequest):
    """Launch visible browser for user to log in and save cookies interactively (Windows-safe)."""
    return await run_sso_bootstrap_api(SSOBootstrapRequest(platform=req.platform, timeout_seconds=req.timeout_seconds))


# =============================================================================
# MODULE EXECUTION ROUTES (WINDOWS-SAFE PLAYWRIGHT EXECUTION)
# =============================================================================
async def _execute_freshness(platform: str, force: bool) -> Dict[str, Any]:
    cron = AlgorithmicFreshnessCron(headless=True, force=force)
    result = {"platform": platform, "success": True}
    if platform in ("naukri", "all"):
        result["naukri"] = await cron.run_naukri()
    if platform in ("bayt", "all"):
        result["bayt"] = await cron.run_bayt()
    proof_file = settings.LOGS_DIR / "naukri_freshness_success.png"
    if proof_file.exists():
        result["proof_image"] = f"/logs/{proof_file.name}?t={datetime.now().timestamp()}"
    return result


@app.post("/api/freshness/run")
async def run_freshness_job(req: FreshnessRequest):
    """Trigger the Algorithmic Freshness Job for Naukri or Bayt (Windows Proactor safe)."""
    try:
        result = await safe_playwright_exec(_execute_freshness, req.platform, req.force)
        return result
    except Exception as e:
        logger.error(f"Failed running freshness job: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e), "message": "Execution error. Please configure credentials in Portal Admin."}
        )


@app.post("/api/leads/search")
async def search_leads_api(req: LeadSearchRequest):
    """Search for founders, CTOs, and VP Engineering at target domain."""
    agent = LeadGenerationCRMAgent()
    leads = await agent.run_pipeline(
        target_domains=[req.domain],
        location=req.location,
    )
    return {
        "domain": req.domain,
        "leads_found": len(leads),
        "leads": [l.model_dump() for l in leads],
    }


@app.get("/api/leads/list")
async def list_crm_leads():
    """Retrieve all leads from the local CSV CRM backup."""
    csv_file = settings.CRM_LOCAL_BACKUP_CSV
    leads: List[Dict[str, str]] = []
    if csv_file.exists():
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                leads.append({
                    "company_name": r.get("Company Name", ""),
                    "domain": r.get("Domain", ""),
                    "contact_name": r.get("Contact Name", ""),
                    "title": r.get("Title", ""),
                    "email": r.get("Email", ""),
                    "location": r.get("Location", ""),
                    "source": r.get("Notes", "apollo.io"),
                })
    return {"count": len(leads), "leads": leads}


@app.post("/api/resume/tailor")
async def tailor_resume_api(req: TailorResumeRequest):
    """LangGraph dynamic resume tailoring endpoint."""
    agent = DynamicResumeTailorAgent()
    result = await agent.tailor_resume(
        jd_text=req.jd,
        company_name=req.company,
        target_location=req.location,
    )

    pdf_p = result.get("pdf_path")
    pdf_url = f"/resumes/{Path(pdf_p).name}" if pdf_p else "#"
    html_url = pdf_url.replace(".pdf", ".html")

    return {
        "status": result.get("status"),
        "company": result.get("company_name"),
        "target_focus_area": result.get("target_focus_area"),
        "top_project": result.get("ranked_projects", [{}])[0].get("name"),
        "tailored_summary": result.get("tailored_summary"),
        "pdf_url": pdf_url,
        "html_url": html_url,
    }


@app.post("/api/outreach/generate")
async def generate_outreach_api(req: OutreachRequest):
    """LangGraph Cover Letter and 150-word InMail drafter endpoint."""
    agent = ContextAwareOutreachAgent()
    result = await agent.generate_outreach(
        jd_text=req.jd,
        company_name=req.company,
        recipient_name=req.name,
        recipient_email=req.email,
        target_location=req.location,
    )
    return {
        "pain_point": result.get("pain_point"),
        "matched_project": result.get("matched_anchor_project", {}).get("name"),
        "cover_letter": result.get("cover_letter_markdown"),
        "direct_message": result.get("direct_message_150words"),
        "draft_status": result.get("gmail_draft_result", {}).get("status"),
    }


async def _execute_auto_apply(url: str, location: str, dry_run: bool) -> Dict[str, Any]:
    orchestrator = AutoApplyOrchestrator(headless=True, dry_run=dry_run)
    result = await orchestrator.apply_to_job(application_url=url, target_location=location)
    proof_p = result.get("proof_screenshot")
    if proof_p:
        result["proof_screenshot"] = f"/logs/{Path(proof_p).name}?t={datetime.now().timestamp()}"
    return result


@app.post("/api/auto-apply/run")
async def auto_apply_api(req: AutoApplyRequest):
    """Playwright Auto-Apply execution endpoint (Windows Proactor safe)."""
    try:
        result = await safe_playwright_exec(_execute_auto_apply, req.url, req.location, req.dry_run)
        return result
    except Exception as e:
        logger.error(f"Auto apply failed: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


sim_sessions: Dict[str, NegotiationSimulatorEngine] = {}

@app.post("/api/negotiation/chat")
async def chat_negotiation_api(req: ChatMessageRequest):
    """Turn-based compensation negotiation sparring endpoint."""
    key = req.scenario
    if key not in sim_sessions:
        sim_sessions[key] = NegotiationSimulatorEngine(scenario=req.scenario)

    engine = sim_sessions[key]
    last_challenge = engine.conversation_history[-1]["content"] if engine.conversation_history else engine.get_initial_opener()

    evaluation = await engine.evaluate_candidate_response(
        candidate_response=req.message,
        last_recruiter_challenge=last_challenge,
    )

    recruiter_reply = await engine.generate_recruiter_counter(candidate_response=req.message)

    return {
        "candidate_message": req.message,
        "recruiter_reply": recruiter_reply,
        "evaluation": evaluation,
    }


# =============================================================================
# CLI RUNNER
# =============================================================================
if __name__ == "__main__":
    import uvicorn
    # Enforce loop="asyncio" with Proactor on Windows and bind to 0.0.0.0 for LAN/Tunnel access
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000, reload=True, loop="asyncio")
