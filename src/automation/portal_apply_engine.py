"""Multi-Platform Job Application Engine for Indeed, Cutshort, and Wellfound.

Provides specialized in-app and external ATS application handling for each portal,
restores platform-specific SSO session cookies, uses Candidate Profile data,
and always captures verification screenshots for manual audit.
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from playwright.async_api import BrowserContext, Page

from config.settings import settings
from src.automation.ats_engine import AdaptiveExternalApplyEngine
from src.automation.browser_factory import BrowserFactory
from src.automation.pending_questions_manager import PendingQuestionsManager
from src.core.profile_repository import ProfileRepository


class MultiPortalApplyEngine:
    """Executes automated job applications specifically tailored to Indeed, Cutshort, and Wellfound."""

    def __init__(self, platform: str, headless: Optional[bool] = None, dry_run: bool = True):
        self.platform = platform.lower()
        self.headless = settings.HEADLESS if headless is None else headless
        self.dry_run = dry_run
        self.profile_repo = ProfileRepository()
        self.candidate = self.profile_repo.load()
        self.pending_mgr = PendingQuestionsManager()

    async def apply_to_job(
        self,
        job_url: str,
        location: str = "Pune, India",
        dry_run: Optional[bool] = None,
        excluded_companies: Optional[List[str]] = None,
        job_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Apply to a job listing on the target platform (Indeed, Cutshort, or Wellfound)."""
        is_dry_run = self.dry_run if dry_run is None else dry_run
        is_dubai = "dubai" in location.lower() or "uae" in location.lower()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if excluded_companies is None:
            excluded_companies = self.profile_repo.get_excluded_companies()

        logger.info(f"[{self.platform.upper()}] Targeting Job URL: {job_url} (DryRun: {is_dry_run})")

        # Initialize browser with this platform's saved SSO cookies
        factory = BrowserFactory(platform_name=self.platform, headless=self.headless)
        context, page = await factory.init_session()

        result = {
            "job_url": job_url,
            "platform": self.platform,
            "location": location,
            "dry_run": is_dry_run,
            "timestamp": timestamp,
            "path": "path_3_external_ats",
            "status": "failed",
            "message": "",
            "proof_screenshot": None,
        }

        try:
            # 1. Navigate to Job URL
            logger.info(f"[{self.platform.upper()}] Navigating to listing: {job_url[:80]}...")
            await page.goto(job_url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(3)

            # 2. Blacklist Check on Company Name
            company_name = (job_info.get("company", "") if job_info else "")
            if not company_name:
                comp_selectors = [
                    "[data-testid='inlineHeader-companyName']",
                    "[data-company-name='true']",
                    ".companyName",
                    "a[href*='/cmp/']",
                    ".company-name",
                    "div.name",
                    "h2",
                ]
                for sel in comp_selectors:
                    el = await page.query_selector(sel)
                    if el:
                        company_name = (await el.inner_text()).strip()
                        if company_name:
                            break

            if company_name and ProfileRepository.is_company_excluded(company_name, excluded_companies):
                logger.warning(f"[{self.platform.upper()}] Aborting: '{company_name}' is blacklisted.")
                result["path"] = "excluded_company"
                result["status"] = "skipped"
                result["message"] = f"Company '{company_name}' matches candidate's exclusion blacklist."
                return result

            # 3. Route to Platform-Specific Handler
            if self.platform == "indeed":
                return await self._apply_indeed(context, page, is_dubai, is_dry_run, timestamp, job_info)
            elif self.platform == "cutshort":
                return await self._apply_cutshort(context, page, is_dubai, is_dry_run, timestamp, job_info)
            elif self.platform == "wellfound":
                return await self._apply_wellfound(context, page, is_dubai, is_dry_run, timestamp, job_info)
            else:
                # Universal fallback
                return await self._apply_universal(context, page, is_dubai, is_dry_run, timestamp, job_info)

        except Exception as e:
            logger.error(f"[{self.platform.upper()}] Application execution error: {e}")
            settings.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            err_proof = settings.SCREENSHOTS_DIR / f"{self.platform}_error_{timestamp}.png"
            try:
                await page.screenshot(path=str(err_proof), full_page=True)
                result["proof_screenshot"] = str(err_proof)
            except Exception:
                pass
            result["status"] = "error"
            result["message"] = str(e)
            return result
        finally:
            try:
                await factory.close()
            except Exception:
                pass

    # =========================================================================
    # INDEED APPLICATION LOGIC
    # =========================================================================
    async def _apply_indeed(
        self,
        context: BrowserContext,
        page: Page,
        is_dubai: bool,
        dry_run: bool,
        timestamp: str,
        job_info: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Handles Indeed in-app apply modal or external redirect to company ATS."""
        logger.info("[Indeed] Analyzing apply triggers on Indeed job view...")

        # Case 1: Look for external apply button (redirects to company ATS)
        external_selectors = [
            "a:has-text('Apply on company site')",
            "button:has-text('Apply on company site')",
            "a[href*='rc/clk']",
            "[data-indeed-apply-joburl]",
            "a:has-text('Apply directly')",
            "button:has-text('Apply directly')",
        ]
        external_btn = None
        for sel in external_selectors:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                external_btn = el
                break

        # Case 2: In-app "Indeed Apply" button
        inapp_selectors = [
            "#indeedApplyButton",
            "button[id*='indeedApply']",
            "button:has-text('Apply now')",
            "button:has-text('Easily apply')",
            ".jobsearch-IndeedApplyButton",
            "button[aria-label*='Apply now']",
        ]
        inapp_btn = None
        for sel in inapp_selectors:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                inapp_btn = el
                break

        pdf_path = ProfileRepository.get_persistent_resume_path() or self.profile_repo.get_resume_path()
        settings.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        if external_btn:
            logger.info("[Indeed] Found 'Apply on company site' button. Redirecting to External ATS...")
            popup_task = asyncio.create_task(self._wait_for_popup(context, timeout=8))
            await external_btn.click()
            await asyncio.sleep(3)
            popup = await popup_task
            target_page = popup or page

            # Engage Adaptive ATS Engine on external site
            ats = AdaptiveExternalApplyEngine(target_page, self.candidate)
            ats_res = await ats.apply_adaptively(
                pdf_path=pdf_path,
                is_dubai=is_dubai,
                dry_run=dry_run,
                max_steps=5,
                job_info=job_info,
            )

            # Always capture verification screenshot
            proof_file = settings.SCREENSHOTS_DIR / f"indeed_path3_verification_{timestamp}.png"
            try:
                await target_page.screenshot(path=str(proof_file), full_page=True)
                proof = str(proof_file)
            except Exception:
                proof = None

            return {
                "job_url": page.url,
                "platform": "indeed",
                "path": "path_3_external_ats",
                "ats_type": ats_res.get("portal_type", "external_ats"),
                "status": ats_res.get("status", "applied"),
                "message": ats_res.get("message") or "Applied via external company career site from Indeed.",
                "proof_screenshot": proof,
                "dry_run": dry_run,
            }

        elif inapp_btn:
            logger.info("[Indeed] Found in-app 'Indeed Apply' button. Engaging Indeed Apply flow...")
            popup_task = asyncio.create_task(self._wait_for_popup(context, timeout=6))
            await inapp_btn.click()
            await asyncio.sleep(3)
            popup = await popup_task
            target_page = popup or page

            # Fill Indeed Apply wizard steps
            await self._step_through_indeed_modal(target_page, pdf_path, dry_run=dry_run)

            proof_file = settings.SCREENSHOTS_DIR / f"indeed_inapp_verification_{timestamp}.png"
            try:
                await target_page.screenshot(path=str(proof_file), full_page=True)
                proof = str(proof_file)
            except Exception:
                proof = None

            status = "applied"
            return {
                "job_url": page.url,
                "platform": "indeed",
                "path": "path_1_easy_apply",
                "status": status,
                "message": "Autofilled Indeed in-app application form with profile credentials & resume.",
                "proof_screenshot": proof,
                "dry_run": dry_run,
            }
        else:
            # Fallback: check if page already has an ATS form
            logger.info("[Indeed] No standard Apply button found, engaging Adaptive External Apply...")
            ats = AdaptiveExternalApplyEngine(page, self.candidate)
            ats_res = await ats.apply_adaptively(
                pdf_path=pdf_path, is_dubai=is_dubai, dry_run=dry_run, max_steps=4, job_info=job_info
            )
            proof_file = settings.SCREENSHOTS_DIR / f"indeed_adaptive_verification_{timestamp}.png"
            try:
                await page.screenshot(path=str(proof_file), full_page=True)
                proof = str(proof_file)
            except Exception:
                proof = None

            return {
                "job_url": page.url,
                "platform": "indeed",
                "path": "path_3_external_ats",
                "status": ats_res.get("status", "applied"),
                "message": ats_res.get("message") or "Processed Indeed listing adaptively.",
                "proof_screenshot": proof,
                "dry_run": dry_run,
            }

    async def _step_through_indeed_modal(self, page: Page, pdf_path: Path, dry_run: bool = True) -> None:
        """Progress through Indeed Apply wizard steps."""
        personal = self.candidate.get("personal", {})
        for step in range(5):
            await asyncio.sleep(2)
            # Fill common contact fields
            name_input = await page.query_selector("input[id*='name'], input[name*='name']")
            if name_input and await name_input.is_visible():
                curr = await name_input.input_value()
                if not curr:
                    await name_input.fill(personal.get("name", "Aneeket Deo"))

            phone_input = await page.query_selector("input[type='tel'], input[id*='phone'], input[name*='phone']")
            if phone_input and await phone_input.is_visible():
                curr = await phone_input.input_value()
                if not curr:
                    await phone_input.fill(personal.get("phone", "+919876543210"))

            # Resume upload if needed
            file_input = await page.query_selector("input[type='file']")
            if file_input:
                try:
                    attach_pdf = ProfileRepository.get_persistent_resume_path() or pdf_path
                    await file_input.set_input_files(str(attach_pdf))
                    logger.info(f"[Indeed] Attached candidate resume: {attach_pdf.name}")
                except Exception:
                    pass

            # Check for Next / Continue button
            continue_btn = await page.query_selector(
                "button:has-text('Continue'), button:has-text('Next'), button[data-testid='continueButton']"
            )
            if continue_btn and await continue_btn.is_visible():
                await continue_btn.click()
                continue

            # Review page or final submit reached
            submit_btn = await page.query_selector(
                "button:has-text('Submit your application'), button:has-text('Submit'), button[id*='submit']"
            )
            if submit_btn and await submit_btn.is_visible():
                if dry_run:
                    logger.info("[Indeed] Dry-Run Mode: Paused at final Indeed submission screen.")
                else:
                    await submit_btn.click()
                    logger.info("[Indeed] Live Mode: Submitted application!")
                break

    # =========================================================================
    # CUTSHORT APPLICATION LOGIC
    # =========================================================================
    async def _apply_cutshort(
        self,
        context: BrowserContext,
        page: Page,
        is_dubai: bool,
        dry_run: bool,
        timestamp: str,
        job_info: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Handles Cutshort Quick Apply or external career site application."""
        logger.info("[Cutshort] Analyzing apply triggers on Cutshort listing...")
        pdf_path = ProfileRepository.get_persistent_resume_path() or self.profile_repo.get_resume_path()
        settings.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        apply_btn = await page.query_selector(
            "button:has-text('Apply'), button:has-text('Quick Apply'), a:has-text('Apply now'), a:has-text('Apply on company site')"
        )
        if apply_btn and await apply_btn.is_visible():
            popup_task = asyncio.create_task(self._wait_for_popup(context, timeout=6))
            await apply_btn.click()
            await asyncio.sleep(2)
            popup = await popup_task

            if popup or "cutshort.io" not in page.url.lower():
                target_page = popup or page
                logger.info("[Cutshort] Redirected to external career portal. Engaging Adaptive ATS...")
                ats = AdaptiveExternalApplyEngine(target_page, self.candidate)
                ats_res = await ats.apply_adaptively(
                    pdf_path=pdf_path, is_dubai=is_dubai, dry_run=dry_run, max_steps=5, job_info=job_info
                )
                proof_file = settings.SCREENSHOTS_DIR / f"cutshort_path3_verification_{timestamp}.png"
                try:
                    await target_page.screenshot(path=str(proof_file), full_page=True)
                    proof = str(proof_file)
                except Exception:
                    proof = None

                return {
                    "job_url": page.url,
                    "platform": "cutshort",
                    "path": "path_3_external_ats",
                    "status": ats_res.get("status", "applied"),
                    "message": ats_res.get("message") or "Applied via external career portal from Cutshort.",
                    "proof_screenshot": proof,
                    "dry_run": dry_run,
                }
            else:
                # In-app Cutshort modal / note
                note_area = await page.query_selector("textarea, [placeholder*='message'], [placeholder*='pitch']")
                if note_area and await note_area.is_visible():
                    pitch = (
                        "Lead GenAI Architect with hands-on production expertise in high-throughput RAG systems, "
                        "multi-agent orchestration (LangGraph, CrewAI), and enterprise LLM deployment. "
                        "Proven record: 60% latency reduction in contract review, 40% MTTR reduction in network ops. "
                        "Immediate availability in Pune / Hybrid."
                    )
                    await note_area.fill(pitch)

                proof_file = settings.SCREENSHOTS_DIR / f"cutshort_inapp_verification_{timestamp}.png"
                try:
                    await page.screenshot(path=str(proof_file), full_page=True)
                    proof = str(proof_file)
                except Exception:
                    proof = None

                final_submit = await page.query_selector(
                    "button:has-text('Send application'), button:has-text('Submit Application'), button:has-text('Send')"
                )
                if final_submit and not dry_run:
                    await final_submit.click()
                    status = "applied"
                    message = "Submitted application pitch directly on Cutshort."
                else:
                    status = "applied"
                    message = "Cutshort application note filled and verified (Dry-Run)."

                return {
                    "job_url": page.url,
                    "platform": "cutshort",
                    "path": "path_1_easy_apply",
                    "status": status,
                    "message": message,
                    "proof_screenshot": proof,
                    "dry_run": dry_run,
                }
        else:
            # Fallback adaptive application
            ats = AdaptiveExternalApplyEngine(page, self.candidate)
            ats_res = await ats.apply_adaptively(pdf_path, is_dubai=is_dubai, dry_run=dry_run, max_steps=3, job_info=job_info)
            proof_file = settings.SCREENSHOTS_DIR / f"cutshort_adaptive_verification_{timestamp}.png"
            try:
                await page.screenshot(path=str(proof_file), full_page=True)
                proof = str(proof_file)
            except Exception:
                proof = None

            return {
                "job_url": page.url,
                "platform": "cutshort",
                "path": "path_3_external_ats",
                "status": ats_res.get("status", "applied"),
                "message": ats_res.get("message") or "Cutshort role processed adaptively.",
                "proof_screenshot": proof,
                "dry_run": dry_run,
            }

    # =========================================================================
    # WELLFOUND (ANGELLIST) APPLICATION LOGIC
    # =========================================================================
    async def _apply_wellfound(
        self,
        context: BrowserContext,
        page: Page,
        is_dubai: bool,
        dry_run: bool,
        timestamp: str,
        job_info: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Handles Wellfound (AngelList) in-app note or external redirect."""
        logger.info("[Wellfound] Analyzing apply triggers on Wellfound role...")
        pdf_path = ProfileRepository.get_persistent_resume_path() or self.profile_repo.get_resume_path()
        settings.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        apply_btn = await page.query_selector(
            "[data-test='JobListingApplyButton'], button:has-text('Apply'), a:has-text('Apply'), a:has-text('Apply on website')"
        )
        if apply_btn and await apply_btn.is_visible():
            popup_task = asyncio.create_task(self._wait_for_popup(context, timeout=6))
            await apply_btn.click()
            await asyncio.sleep(2)
            popup = await popup_task

            if popup or "wellfound.com" not in page.url.lower():
                target_page = popup or page
                ats = AdaptiveExternalApplyEngine(target_page, self.candidate)
                ats_res = await ats.apply_adaptively(
                    pdf_path=pdf_path, is_dubai=is_dubai, dry_run=dry_run, max_steps=5, job_info=job_info
                )
                proof_file = settings.SCREENSHOTS_DIR / f"wellfound_path3_verification_{timestamp}.png"
                try:
                    await target_page.screenshot(path=str(proof_file), full_page=True)
                    proof = str(proof_file)
                except Exception:
                    proof = None

                return {
                    "job_url": page.url,
                    "platform": "wellfound",
                    "path": "path_3_external_ats",
                    "status": ats_res.get("status", "applied"),
                    "message": ats_res.get("message") or "Applied via startup career portal from Wellfound.",
                    "proof_screenshot": proof,
                    "dry_run": dry_run,
                }
            else:
                # In-app note drawer
                note_el = await page.query_selector("textarea, [placeholder*='note'], [placeholder*='Tell the founder']")
                if note_el and await note_el.is_visible():
                    note = (
                        "Hi team, I specialize in building agentic AI and high-precision RAG systems. "
                        "Recently architected enterprise RAG workflows cutting contract review delays by 60% "
                        "and autonomous triage agents driving 70% zero-touch resolution. "
                        "Excited to contribute to your GenAI platform!"
                    )
                    await note_el.fill(note)

                proof_file = settings.SCREENSHOTS_DIR / f"wellfound_inapp_verification_{timestamp}.png"
                try:
                    await page.screenshot(path=str(proof_file), full_page=True)
                    proof = str(proof_file)
                except Exception:
                    proof = None

                send_btn = await page.query_selector(
                    "button:has-text('Send application'), button:has-text('Apply'), [data-test='ApplyModalSubmitButton']"
                )
                if send_btn and not dry_run:
                    await send_btn.click()
                    status = "applied"
                    message = "Submitted application note directly on Wellfound."
                else:
                    status = "applied"
                    message = "Wellfound application note verified (Dry-Run)."

                return {
                    "job_url": page.url,
                    "platform": "wellfound",
                    "path": "path_1_easy_apply",
                    "status": status,
                    "message": message,
                    "proof_screenshot": proof,
                    "dry_run": dry_run,
                }
        else:
            ats = AdaptiveExternalApplyEngine(page, self.candidate)
            ats_res = await ats.apply_adaptively(pdf_path, is_dubai=is_dubai, dry_run=dry_run, max_steps=3, job_info=job_info)
            proof_file = settings.SCREENSHOTS_DIR / f"wellfound_adaptive_verification_{timestamp}.png"
            try:
                await page.screenshot(path=str(proof_file), full_page=True)
                proof = str(proof_file)
            except Exception:
                proof = None

            return {
                "job_url": page.url,
                "platform": "wellfound",
                "path": "path_3_external_ats",
                "status": ats_res.get("status", "applied"),
                "message": ats_res.get("message") or "Wellfound role processed adaptively.",
                "proof_screenshot": proof,
                "dry_run": dry_run,
            }

    async def _apply_universal(
        self,
        context: BrowserContext,
        page: Page,
        is_dubai: bool,
        dry_run: bool,
        timestamp: str,
        job_info: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Universal adaptive handler for other external roles."""
        pdf_path = ProfileRepository.get_persistent_resume_path() or self.profile_repo.get_resume_path()
        settings.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        ats = AdaptiveExternalApplyEngine(page, self.candidate)
        ats_res = await ats.apply_adaptively(pdf_path, is_dubai=is_dubai, dry_run=dry_run, max_steps=5, job_info=job_info)
        proof_file = settings.SCREENSHOTS_DIR / f"{self.platform}_verification_{timestamp}.png"
        try:
            await page.screenshot(path=str(proof_file), full_page=True)
            proof = str(proof_file)
        except Exception:
            proof = None

        return {
            "job_url": page.url,
            "platform": self.platform,
            "path": "path_3_external_ats",
            "status": ats_res.get("status", "applied"),
            "message": ats_res.get("message") or f"Processed {self.platform} application adaptively.",
            "proof_screenshot": proof,
            "dry_run": dry_run,
        }

    async def _wait_for_popup(self, context: BrowserContext, timeout: float = 6.0) -> Optional[Page]:
        try:
            return await context.wait_for_event("page", timeout=timeout * 1000)
        except Exception:
            return None
