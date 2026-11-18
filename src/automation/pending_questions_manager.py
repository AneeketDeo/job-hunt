import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from src.core.logger import logger
from src.core.profile_repository import ProfileRepository


class PendingQuestionsManager:
    """Manages screening questions that could not be confidently answered by the Profile Repository.

    Prompts the candidate for input and coordinates auto-apply re-execution once resolved.
    """

    FILE_PATH = settings.DATA_DIR / "pending_questions.json"

    def __init__(self):
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not self.FILE_PATH.exists():
            self.FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self.FILE_PATH, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=2)

    def load_pending(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self.FILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error loading {self.FILE_PATH}: {e}")
            return {}

    def save_pending(self, data: Dict[str, Dict[str, Any]]) -> None:
        with open(self.FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def add_pending_question(
        self,
        job_id: str,
        title: str,
        company: str,
        url: str,
        location: str,
        question: str,
        choices: Optional[List[str]] = None,
        proof_screenshot: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record an unanswered screening question requiring candidate input."""
        pending = self.load_pending()
        entry = {
            "job_id": job_id,
            "title": title,
            "company": company,
            "url": url,
            "location": location,
            "question": question,
            "choices": choices or [],
            "question_type": "choice" if choices else "text",
            "proof_screenshot": proof_screenshot,
            "status": "pending_candidate_answer",
            "detected_at": datetime.now().isoformat(),
        }
        pending[job_id] = entry
        self.save_pending(pending)
        logger.info(
            f"Logged unanswered screening question for [{title} @ {company}]: '{question}' (Choices: {choices})"
        )
        return entry

    def remove_pending(self, job_id: str) -> bool:
        """Remove a question from pending once answered."""
        pending = self.load_pending()
        if job_id in pending:
            del pending[job_id]
            self.save_pending(pending)
            logger.info(f"Removed job [{job_id}] from pending questions queue.")
            return True
        return False

    async def resolve_and_reapply(
        self,
        job_id: str,
        question: str,
        answer: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Save candidate answer to profile repository and immediately re-trigger auto-apply."""
        logger.info(f"Resolving pending question for job [{job_id}]: '{question}' -> '{answer}'")
        repo = ProfileRepository()
        repo.add_qa_answer(question, answer)

        pending = self.load_pending()
        job_info = pending.get(job_id, {})
        job_url = job_info.get("url")
        job_location = job_info.get("location", "Pune, India")

        # Import apply engine and safe executor dynamically to prevent circular imports
        from src.automation.browser_factory import safe_playwright_exec
        from src.automation.naukri_apply_engine import NaukriAutoApplyEngine
        from src.automation.naukri_scraper import NaukriJobScraper

        scraper = NaukriJobScraper()
        ledger = scraper.load_applied_ledger()

        if not job_url:
            # Fallback to ledger
            job_url = ledger.get(job_id, {}).get("url")

        if not job_url:
            return {"status": "error", "message": f"Job URL not found for ID: {job_id}"}

        # Execute re-application via Windows-safe Proactor loop
        async def _reapply_job(target_url: str, target_loc: str, is_dry: bool):
            apply_engine = NaukriAutoApplyEngine(headless=True, dry_run=is_dry)
            return await apply_engine.apply_to_job(
                job_url=target_url,
                location=target_loc,
                dry_run=is_dry,
            )

        app_res = await safe_playwright_exec(_reapply_job, job_url, job_location, dry_run)

        # Update ledger
        ledger[job_id] = {
            "job_id": job_id,
            "title": job_info.get("title") or ledger.get(job_id, {}).get("title", "GenAI Position"),
            "company": job_info.get("company") or ledger.get(job_id, {}).get("company", "Target Company"),
            "url": job_url,
            "location": job_location,
            "applied_at": datetime.now().isoformat(),
            "path": app_res.get("path", "path_2_in_app_questionnaire"),
            "status": app_res.get("status", "applied"),
            "dry_run": dry_run,
            "proof_screenshot": app_res.get("proof_screenshot"),
            "message": app_res.get("message"),
        }
        scraper.save_applied_ledger(ledger)

        # If successfully applied or completed, remove from pending
        if app_res.get("status") in ["applied", "applied_successfully", "dry_run_verified"]:
            self.remove_pending(job_id)

        return {
            "status": "resolved_and_applied",
            "job_id": job_id,
            "answer_saved": answer,
            "application_result": app_res,
        }
