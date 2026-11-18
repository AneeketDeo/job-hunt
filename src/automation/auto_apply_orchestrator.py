import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import httpx
from rich.console import Console
from rich.panel import Panel

from config.settings import settings
from src.core.logger import logger
from src.automation.browser_factory import BrowserFactory
from src.automation.workday_engine import WorkdayAutoApplyEngine
from src.automation.ats_engine import CustomATSEngine


class AutoApplyOrchestrator:
    """Module 5: Multi-Platform Auto-Apply Engine

    Orchestrates automated job applications across Workday, Lever, Greenhouse,
    and custom ATS portals with Dubai salary formatting and fallback intervention webhooks.
    """

    def __init__(self, headless: Optional[bool] = None, dry_run: bool = True):
        self.headless = settings.HEADLESS if headless is None else headless
        self.dry_run = dry_run
        from src.core.profile_repository import ProfileRepository
        self.profile_repo = ProfileRepository()
        self.candidate = self.profile_repo.load()
        self.console = Console()

    def _load_profile_data(self) -> Dict[str, Any]:
        from src.core.profile_repository import ProfileRepository
        return ProfileRepository().load()

    def find_latest_resume_pdf(self) -> Path:
        """Locate the most recently generated tailored PDF resume."""
        pdf_dir = settings.GENERATED_RESUMES_DIR
        pdf_files = list(pdf_dir.glob("*.pdf"))
        if not pdf_files:
            raise FileNotFoundError(
                f"No tailored PDF resume found in [{pdf_dir}]. "
                "Run Module 3 (Dynamic Resume Tailoring) first to compile a tailored resume."
            )
        pdf_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        return pdf_files[0]

    async def send_fallback_webhook(self, url: str, reason: str, screenshot_path: Path) -> None:
        """Send diagnostic webhook notification if manual intervention is required."""
        if not settings.FALLBACK_WEBHOOK_URL:
            logger.warning("FALLBACK_WEBHOOK_URL not configured. Skipping webhook alert.")
            return

        payload = {
            "content": (
                f"⚠️ **Auto-Apply Manual Intervention Required** ⚠️\n"
                f"**Target Application:** {url}\n"
                f"**Reason:** {reason}\n"
                f"**Screenshot Proof:** `{screenshot_path.name}`\n"
                f"**Action Required:** Please review the browser tab or saved screenshot to complete manual fields."
            )
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(settings.FALLBACK_WEBHOOK_URL, json=payload)
                logger.info("Sent fallback webhook alert for manual intervention.")
        except Exception as e:
            logger.error(f"Failed delivering fallback webhook: {e}")

    async def apply_to_job(
        self,
        application_url: str,
        resume_pdf: Optional[Path] = None,
        target_location: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply to a target job application URL."""
        self.console.print("\n[bold cyan]═══ Module 5: Multi-Platform Auto-Apply Engine ═══[/bold cyan]")
        logger.info(f"Targeting Application URL: {application_url}")

        # Platform Routing: If target is a Naukri job, delegate to dedicated 3-path engine
        if "naukri.com" in application_url.lower():
            logger.info("Detected Naukri application URL. Delegating to 3-Path NaukriAutoApplyEngine...")
            from src.automation.naukri_apply_engine import NaukriAutoApplyEngine
            naukri_engine = NaukriAutoApplyEngine(headless=self.headless, dry_run=self.dry_run)
            return await naukri_engine.apply_to_job(
                job_url=application_url,
                location=target_location or "Pune, India",
                dry_run=self.dry_run,
            )

        pdf_path = resume_pdf or self.find_latest_resume_pdf()
        logger.info(f"Using Tailored Resume: {pdf_path.name}")

        is_dubai = (
            "dubai" in (target_location or "").lower()
            or "uae" in (target_location or "").lower()
            or ".ae" in application_url
        )

        factory = BrowserFactory(platform_name="auto_apply", headless=self.headless)
        context, page = await factory.init_session()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        status_result = {"url": application_url, "status": "initiated", "dry_run": self.dry_run}

        try:
            logger.info("Navigating to application page...")
            await page.goto(application_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(4)

            # Check for blocking CAPTCHAs upfront
            if await page.query_selector("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .cf-turnstile"):
                reason = "Anti-bot CAPTCHA challenge detected on application portal."
                logger.warning(reason)
                screenshot = settings.LOGS_DIR / f"auto_apply_intervention_captcha_{timestamp}.png"
                await page.screenshot(path=str(screenshot))
                await self.send_fallback_webhook(application_url, reason, screenshot)
                return {"status": "manual_intervention_required", "reason": reason, "screenshot": str(screenshot)}

            # Platform Routing
            lower_url = application_url.lower()

            if "myworkdayjobs.com" in lower_url or "workday" in lower_url:
                logger.info("Detected Workday ATS platform.")
                workday = WorkdayAutoApplyEngine(page, self.candidate)
                await workday.upload_resume(pdf_path)
                await workday.fill_candidate_info(is_dubai=is_dubai)
                await workday.handle_regional_questionnaire(is_dubai=is_dubai)
                status_result["ats_type"] = "workday"

            elif "lever.co" in lower_url or "greenhouse.io" in lower_url or True:
                ats = CustomATSEngine(page, self.candidate)
                adapt_res = await ats.apply_adaptively(pdf_path, is_dubai=is_dubai, dry_run=self.dry_run)
                status_result["ats_type"] = adapt_res.get("portal_type", "custom_ats")
                status_result["status"] = adapt_res.get("status", "applied")

            # Capture verification screenshot of populated form
            proof_screenshot = settings.LOGS_DIR / f"auto_apply_proof_{timestamp}.png"
            await page.screenshot(path=str(proof_screenshot), full_page=True)
            logger.info(f"Captured form completion proof: {proof_screenshot}")
            status_result["proof_screenshot"] = str(proof_screenshot)

            # Final Action
            if self.dry_run:
                logger.info("DRY-RUN MODE: Form populated and verified. Final submit button intentionally skipped.")
                status_result["status"] = "dry_run_completed_verified"
            else:
                logger.info("LIVE MODE: Locating and submitting application...")
                submit_btn = await page.query_selector(
                    "button[type='submit']:has-text('Submit'), button:has-text('Submit Application'), input[type='submit']"
                )
                if submit_btn:
                    await submit_btn.click()
                    logger.info("Application submitted successfully!")
                    status_result["status"] = "submitted"
                    await asyncio.sleep(4)
                else:
                    logger.warning("Could not identify unambiguous submit button; manual confirmation advised.")
                    status_result["status"] = "form_filled_submit_pending"

            return status_result

        except Exception as e:
            logger.error(f"Error during auto-apply sequence: {e}")
            err_screenshot = settings.LOGS_DIR / f"auto_apply_error_{timestamp}.png"
            try:
                await page.screenshot(path=str(err_screenshot))
            except Exception:
                pass
            await self.send_fallback_webhook(application_url, str(e), err_screenshot)
            status_result["status"] = "error"
            status_result["error"] = str(e)
            return status_result

        finally:
            await factory.close()


# =============================================================================
# CLI ENTRY POINT
# =============================================================================
async def main():
    parser = argparse.ArgumentParser(
        description="Job Hunt Orchestrator - Module 5: Multi-Platform Auto-Apply Engine (Playwright)"
    )
    parser.add_argument(
        "--url",
        type=str,
        default="https://jobs.lever.co/sample-company/sample-job",
        help="Target application form URL (Workday, Lever, Greenhouse, etc.)",
    )
    parser.add_argument(
        "--resume",
        type=str,
        help="Path to specific tailored PDF resume to upload (defaults to latest generated)",
    )
    parser.add_argument(
        "--location",
        choices=["Dubai, UAE", "Pune, India"],
        default="Dubai, UAE",
        help="Target job location (governs salary & residency formatting)",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Submit application live (default is safe dry-run mode)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run browser headed (visible) to inspect auto-fill live",
    )

    args = parser.parse_args()
    console = Console()

    dry_run = not args.submit
    headless = False if args.interactive else None
    pdf_path = Path(args.resume) if args.resume else None

    orchestrator = AutoApplyOrchestrator(headless=headless, dry_run=dry_run)
    result = await orchestrator.apply_to_job(
        application_url=args.url,
        resume_pdf=pdf_path,
        target_location=args.location,
    )

    console.print(Panel(
        f"[bold green]Application URL:[/bold green] {result.get('url')}\n"
        f"[bold green]ATS Platform:[/bold green] {result.get('ats_type', 'generic')}\n"
        f"[bold green]Execution Status:[/bold green] {result.get('status')}\n"
        f"[bold green]Dry-Run Mode:[/bold green] {result.get('dry_run')}\n"
        f"[bold green]Proof Screenshot:[/bold green] {result.get('proof_screenshot', 'N/A')}",
        title="🤖 Auto-Apply Execution Result",
        border_style="cyan"
    ))


if __name__ == "__main__":
    asyncio.run(main())
