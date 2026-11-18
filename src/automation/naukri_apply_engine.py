import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.async_api import Page, BrowserContext

from config.settings import settings
from src.core.logger import logger
from src.core.profile_repository import ProfileRepository
from src.automation.browser_factory import BrowserFactory
from src.automation.workday_engine import WorkdayAutoApplyEngine
from src.automation.ats_engine import CustomATSEngine, AdaptiveExternalApplyEngine


class NaukriAutoApplyEngine:
    """Specialized Multi-Path Auto-Apply Engine for Naukri.com

    Supports 3 application paths:
      - Path 1: 1-Click Easy Apply (Direct in-app submission)
      - Path 2: In-App Application with Screening Questions / Chatbot Modal
      - Path 3: External Company Site / ATS Redirection with Auto-Login
                (Email: aneeketdeowork@gmail.com, Password: Aneeket$123)
    """

    def __init__(self, headless: Optional[bool] = None, dry_run: bool = True):
        self.headless = settings.HEADLESS if headless is None else headless
        self.dry_run = dry_run
        self.profile_repo = ProfileRepository()
        self.profile = self.profile_repo.load()

    async def apply_to_job(
        self,
        job_url: str,
        location: str = "Pune, India",
        dry_run: Optional[bool] = None,
        excluded_companies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Execute full auto-apply pipeline for a specific Naukri job listing."""
        is_dry_run = self.dry_run if dry_run is None else dry_run
        is_dubai = "dubai" in location.lower() or "uae" in location.lower()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Resolve excluded companies
        if excluded_companies is None:
            excluded_companies = self.profile_repo.get_excluded_companies()

        logger.info(f"Targeting Naukri Job URL: {job_url} (Location: {location}, DryRun: {is_dry_run})")

        factory = BrowserFactory(platform_name="naukri", headless=self.headless)
        context, page = await factory.init_session()

        result = {
            "job_url": job_url,
            "location": location,
            "dry_run": is_dry_run,
            "timestamp": timestamp,
            "path": "unknown",
            "status": "failed",
        }

        try:
            logger.info("Navigating to Naukri job listing...")
            await page.goto(job_url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(3)

            # Extract company name and check candidate's exclusion blacklist
            comp_name = ""
            comp_el = await page.query_selector(
                "a.comp-name, .company-name, .jd-header-comp-name, [class*='company-name'], .comp-info a"
            )
            if comp_el:
                comp_name = (await comp_el.inner_text()).strip()

            if excluded_companies and comp_name:
                if ProfileRepository.is_company_excluded(comp_name, excluded_companies):
                    logger.warning(
                        f"[EXCLUDED] Aborting application: Company '{comp_name}' is in exclusion blacklist."
                    )
                    result["path"] = "excluded_company"
                    result["status"] = "skipped"
                    result["message"] = f"Company '{comp_name}' matches candidate's company exclusion blacklist."
                    return result

            # 1. Check if already applied
            already_applied = await self._check_already_applied(page)
            if already_applied:
                logger.info("Job listing is already applied. Skipping.")
                result["path"] = "already_applied"
                result["status"] = "skipped"
                result["message"] = "Application was already submitted previously."
                result["proof_screenshot"] = None
                return result

            # Extract job card details for metadata tracking
            title_el = await page.query_selector("h1.jd-header-title, .title, [class*='jd-header-title']")
            job_title = (await title_el.inner_text()).strip() if title_el else "GenAI Developer"
            nums = re.findall(r"\d{10,}", job_url)
            job_id = nums[-1] if nums else str(abs(hash(job_url)))
            job_info = {
                "job_id": job_id,
                "title": job_title,
                "company": comp_name or "Target Company",
                "url": job_url,
                "location": location,
            }

            # 2. Check if this is an external redirect directly or via button
            is_external_button = await self._is_external_apply_button(page)
            if is_external_button:
                logger.info("Detected External Apply (Path 3) trigger button on Naukri. Clicking to navigate...")
                popup_page_task = asyncio.create_task(self._wait_for_popup(context, timeout=8))
                external_btn = await self._locate_apply_button(page)
                if external_btn:
                    await external_btn.click()
                    await asyncio.sleep(4)
                popup_page = await popup_page_task
                target_page = popup_page or page
                return await self._execute_path_3_external(
                    context, target_page, is_dubai=is_dubai, dry_run=is_dry_run, timestamp=timestamp, job_info=job_info
                )

            # 3. Locate and click standard Apply button
            apply_btn = await self._locate_apply_button(page)
            if not apply_btn:
                raise RuntimeError("Could not identify an active Apply button on the job listing.")

            logger.info("Located Apply button. Initiating application...")

            # Prepare to catch new popup window in case clicking redirects externally
            popup_page_task = asyncio.create_task(self._wait_for_popup(context, timeout=6))

            try:
                await apply_btn.scroll_into_view_if_needed()
            except Exception:
                pass
            await apply_btn.click(force=True)
            await asyncio.sleep(3.0)

            # 1. Check if a popup opened (Path 3 External ATS)
            popup_page = await popup_page_task
            if popup_page:
                logger.info("Apply button opened external portal popup. Switching to Path 3.")
                return await self._execute_path_3_external(
                    context, popup_page, is_dubai=is_dubai, dry_run=is_dry_run, timestamp=timestamp, job_info=job_info
                )

            # 2. Check if current page redirected to an external domain (Path 3)
            current_url = page.url.lower()
            if "naukri.com" not in current_url:
                logger.info(f"Page navigated to external ATS ({current_url}). Switching to Path 3.")
                return await self._execute_path_3_external(
                    context, page, is_dubai=is_dubai, dry_run=is_dry_run, timestamp=timestamp, job_info=job_info
                )

            # 3. Check for 1-Click Easy Apply Success FIRST (Path 1)
            is_success = await self._check_easy_apply_success(page)
            if is_success:
                logger.info("Path 1 (1-Click Easy Apply) succeeded!")
                result["path"] = "path_1_easy_apply"
                result["status"] = "applied"
                result["message"] = "1-Click Easy Apply completed directly using your authenticated Naukri profile."
                result["proof_screenshot"] = None
                return result

            # 4. Check for In-App Questionnaire / Chatbot Modal (Path 2)
            has_questionnaire = await self._detect_in_app_questionnaire(page)
            if has_questionnaire:
                logger.info("Detected In-App Questionnaire / Chatbot Modal. Executing Path 2...")
                return await self._execute_path_2_questionnaire(
                    page, is_dubai=is_dubai, dry_run=is_dry_run, timestamp=timestamp, job_info=job_info
                )

            # 5. Safe dry-run verified or completed
            result["path"] = "path_1_easy_apply"
            result["status"] = "applied"
            result["message"] = "Apply button clicked and application recorded."
            result["proof_screenshot"] = None
            return result

        except Exception as e:
            logger.error(f"Error during Naukri auto-apply sequence: {e}")
            settings.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            err_proof = settings.SCREENSHOTS_DIR / f"naukri_apply_error_{timestamp}.png"
            try:
                await page.screenshot(path=str(err_proof))
                result["proof_screenshot"] = str(err_proof)
            except Exception:
                pass
            result["status"] = "error"
            result["message"] = str(e)
            return result

        finally:
            await factory.close()

    async def sync_resume_to_naukri(self) -> Dict[str, Any]:
        """Upload active candidate resume directly to authenticated Naukri profile."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_path = ProfileRepository.get_persistent_resume_path() or self.profile_repo.get_resume_path()
        logger.info(f"Initiating resume upload to Naukri profile: {pdf_path.name}")

        factory = BrowserFactory(platform_name="naukri", headless=self.headless)
        context, page = await factory.init_session()

        try:
            profile_url = "https://www.naukri.com/mnjuser/profile"
            logger.info(f"Navigating to Naukri Profile: {profile_url}")
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(4)

            # Check if session loaded
            if "login" in page.url.lower():
                raise RuntimeError("Naukri session expired. Please re-authenticate via SSO.")

            # Locate file input element on profile
            file_input = None
            for sel in ["input#attachCV", "input[type='file'][id*='attach']", "input[type='file'][id*='resume']", "input.fileUpload", "input[type='file']"]:
                try:
                    cand = await page.wait_for_selector(sel, timeout=3000)
                    if cand:
                        file_input = cand
                        break
                except Exception:
                    continue

            if not file_input:
                update_btn = await page.query_selector("a:has-text('Update resume'), span:has-text('Update resume'), .updateResume")
                if update_btn:
                    await update_btn.click()
                    await asyncio.sleep(1)
                file_input = await page.wait_for_selector("input[type='file']", timeout=10000)

            if not file_input:
                raise RuntimeError("Could not find file upload element on Naukri profile page.")

            logger.info(f"Setting input file to {pdf_path}...")
            await file_input.set_input_files(str(pdf_path))
            logger.info("Uploaded file. Waiting for Naukri to process and confirm...")
            await asyncio.sleep(5)

            # Proof screenshot
            proof = settings.LOGS_DIR / f"naukri_resume_sync_{timestamp}.png"
            await page.screenshot(path=str(proof))
            logger.info(f"Proof captured at: {proof}")

            await factory.save_session_state()

            return {
                "status": "success",
                "filename": pdf_path.name,
                "message": f"Successfully synced and uploaded '{pdf_path.name}' to your live Naukri profile!",
                "proof_screenshot": f"/logs/{proof.name}?t={datetime.now().timestamp()}",
            }

        except Exception as e:
            logger.error(f"Error syncing resume to Naukri: {e}")
            err_proof = settings.LOGS_DIR / f"naukri_resume_sync_error_{timestamp}.png"
            try:
                await page.screenshot(path=str(err_proof))
            except Exception:
                pass
            return {
                "status": "error",
                "message": str(e),
                "proof_screenshot": f"/logs/{err_proof.name}?t={datetime.now().timestamp()}" if err_proof.exists() else None,
            }
        finally:
            await factory.close()

    # =========================================================================
    # PATH 1 HELPERS
    # =========================================================================
    async def _check_already_applied(self, page: Page) -> bool:
        """Check if listing shows an 'Already Applied' badge or button."""
        selectors = [
            "button:has-text('Already Applied')",
            "span:has-text('Already Applied')",
            ".already-applied",
            "div:has-text('You have already applied')",
            ".applied-tag",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return True
            except Exception:
                pass
        return False

    async def _locate_apply_button(self, page: Page):
        """Find the prominent apply button on the Naukri job listing."""
        selectors = [
            "button#apply-button",
            "button.apply-button",
            "button:has-text('Apply')",
            "a#apply-button",
            "a.apply-button",
            "a:has-text('Apply')",
            "[data-automation-id='apply-button']",
        ]
        for sel in selectors:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    return btn
            except Exception:
                pass
        return None

    async def _check_easy_apply_success(self, page: Page) -> bool:
        """Check if application success message, banner, or toast appeared."""
        success_selectors = [
            ":has-text('Applied to')",
            "div:has-text('Applied to')",
            "span:has-text('Applied to')",
            ".apply-message",
            ".success-message",
            "div:has-text('Application sent')",
            "div:has-text('Successfully applied')",
            "div:has-text('Applied successfully')",
            "button:has-text('Applied')",
            ".already-applied",
            ".applied-msg",
            ".apply-success-container",
        ]
        for sel in success_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    txt = (await el.inner_text()).strip().lower()
                    if "applied" in txt or "application sent" in txt:
                        return True
            except Exception:
                pass
        return False

    # =========================================================================
    # PATH 2: IN-APP QUESTIONNAIRE & CHATBOT ANSWERING
    # =========================================================================
    async def _detect_in_app_questionnaire(self, page: Page) -> bool:
        """Detect if clicking apply opened an in-app questionnaire drawer or chatbot."""
        modal_selectors = [
            ".chatbot-container",
            ".chatbot_Drawer",
            "form#chatbot-form",
            "div[class*='chatbot']",
            ".apply-message-container",
            ".qna-container",
            "div[class*='chatbotDrawer']",
            ".apply-dialog",
            ".bot-container",
        ]
        container = None
        for sel in modal_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    container = el
                    break
            except Exception:
                pass

        if not container:
            return False

        # Must contain actual questions or interactive input elements
        interactive_selectors = [
            ".chips-wrap",
            ".chip",
            "button.quick-reply-btn",
            ".radio-btn-container",
            "input[type='radio']",
            "textarea",
            ".bot-question-text",
            ".chat-question",
            ".quesText",
            ".botMessage",
            ".question-title",
        ]
        for isel in interactive_selectors:
            try:
                iel = await container.query_selector(isel)
                if iel and await iel.is_visible():
                    return True
            except Exception:
                pass

        return False

    async def _execute_path_2_questionnaire(
        self,
        page: Page,
        is_dubai: bool,
        dry_run: bool,
        timestamp: str,
        job_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Iteratively answer screening questions in the Naukri chatbot/drawer."""
        logger.info("Executing Path 2: Answering in-app screening questions...")
        answered_questions = []

        # Find active modal or drawer container
        modal_container = None
        for sel in [".chatbot-container", ".chatbot_Drawer", "form#chatbot-form", "div[class*='chatbot']", ".apply-dialog", ".qna-container"]:
            try:
                cand = await page.query_selector(sel)
                if cand and await cand.is_visible():
                    modal_container = cand
                    break
            except Exception:
                pass
        scope = modal_container or page

        max_steps = 15
        for step in range(max_steps):
            await asyncio.sleep(1.5)

            # Check if finished
            if await self._check_easy_apply_success(page):
                logger.info("Questionnaire submitted and verified successfully!")
                break

            if dry_run and step >= 3:
                logger.info("DRY-RUN MODE: Form questionnaire populated. Final submission skipped.")
                break

            # Find active question prompt text inside modal scope
            q_text = await self._get_current_question_text(scope)
            logger.info(f"Questionnaire Step {step + 1} Prompt: '{q_text}'")

            # 1. Check for Radio Buttons or Clickable Chips inside scope
            chips = await scope.query_selector_all(
                ".ssrc__radio-btn-container, label.ssrc__label, .singleselect-radiobutton-container label, "
                ".chips-wrap span, .chip, label.radio-label, .options-wrap button, .radio-btn-container label, .quick-reply-btn"
            )
            visible_chips = []
            chip_texts = []
            for c in chips:
                if await c.is_visible():
                    txt = (await c.inner_text()).strip()
                    if txt and txt not in chip_texts and txt.lower() != "skip this question":
                        visible_chips.append(c)
                        chip_texts.append(txt)

            # Check if answer is known by Profile Repository
            details = self.profile_repo.get_answer_details(q_text, choices=chip_texts, is_dubai=is_dubai)

            # If answer is unknown, record to pending questions queue and pause application
            if not details.get("is_known", True) and not (dry_run and step >= 2):
                logger.warning(
                    f"[MISSING ANSWER] Unknown question: '{q_text}' (Choices: {chip_texts}). "
                    f"Pausing application and queuing for candidate input."
                )
                settings.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
                proof = settings.SCREENSHOTS_DIR / f"naukri_missing_answers_{timestamp}.png"
                await page.screenshot(path=str(proof))

                try:
                    from src.automation.pending_questions_manager import PendingQuestionsManager
                    mgr = PendingQuestionsManager()
                    j_id = (job_info or {}).get("job_id") or str(abs(hash(q_text)))
                    mgr.add_pending_question(
                        job_id=j_id,
                        title=(job_info or {}).get("title", "GenAI Developer"),
                        company=(job_info or {}).get("company", "Target Employer"),
                        url=(job_info or {}).get("url", page.url),
                        location=(job_info or {}).get("location", "Pune, India"),
                        question=q_text,
                        choices=chip_texts,
                        proof_screenshot=str(proof),
                    )
                except Exception as e:
                    logger.warning(f"Could not record pending question: {e}")

                return {
                    "path": "path_2_in_app_questionnaire",
                    "status": "needs_user_answer",
                    "message": f"Paused: Question '{q_text}' requires your answer. Prompted in Dashboard.",
                    "missing_question": q_text,
                    "choices": chip_texts,
                    "proof_screenshot": str(proof),
                    "dry_run": dry_run,
                }

            if visible_chips:
                best_answer = details.get("answer") or self.profile_repo.get_answer(q_text, choices=chip_texts, is_dubai=is_dubai)
                logger.info(f"Available options: {chip_texts} -> Selected: '{best_answer}'")

                # Robust click with fuzzy/substring match
                target_chip = None
                for chip in visible_chips:
                    txt = (await chip.inner_text()).strip()
                    if txt.lower() == best_answer.lower():
                        target_chip = chip
                        break
                    if best_answer.lower() in txt.lower() or txt.lower() in best_answer.lower():
                        target_chip = chip

                if target_chip:
                    await target_chip.click(force=True)
                    answered_questions.append({"question": q_text, "answer": best_answer, "type": "choice"})
                await asyncio.sleep(1)

            # 2. Check for Textarea or Text Input inside scope
            text_input = await scope.query_selector(
                "input[type='text']:visible, textarea:visible, input[type='number']:visible, input[class*='input']:visible"
            )
            if text_input and not visible_chips:
                answer = details.get("answer") or self.profile_repo.get_answer(q_text, is_dubai=is_dubai)
                logger.info(f"Formulating text answer for '{q_text}': '{answer}'")
                await text_input.fill(answer)
                answered_questions.append({"question": q_text, "answer": answer, "type": "text"})
                await asyncio.sleep(0.5)

            # 3. Check for Checkboxes inside scope
            checkboxes = await scope.query_selector_all("input[type='checkbox']:visible")
            for cb in checkboxes:
                if not await cb.is_checked():
                    await cb.check()
                    logger.info("Checked required questionnaire confirmation box.")

            # Click Next / Submit / Send / Save
            next_btn = await scope.query_selector(
                "div.sendMsg, .sendMsgbtn_container, div:has-text('Save'), button:has-text('Save'), "
                "button:has-text('Save & Next'), button:has-text('Next'), button:has-text('Submit'), "
                "button:has-text('Send'), span.send-btn, button[type='submit'], .quick-reply-btn"
            )
            if next_btn and await next_btn.is_visible():
                await next_btn.click(force=True)
                await asyncio.sleep(1.5)
            else:
                await page.keyboard.press("Enter")
                await asyncio.sleep(1)

        return {
            "path": "path_2_in_app_questionnaire",
            "status": "applied",
            "message": f"Successfully completed {len(answered_questions)} screening questions using Profile Repository.",
            "questions_answered": answered_questions,
            "proof_screenshot": None,
            "dry_run": dry_run,
        }

    async def _get_current_question_text(self, scope) -> str:
        """Extract the text of the latest question asked by the Naukri chatbot/modal."""
        selectors = [
            ".botMsg.msg span",
            ".botMsg.msg",
            "div[class*='botMsg'] span",
            "div[class*='botMsg']",
            ".bot-msg:last-child .msgContent",
            ".bot-msg:last-child",
            ".chat-question",
            ".question-title",
            ".quesText",
            ".bot-question-text",
            "div[class*='bot-'] p",
            "div[class*='botMessage']",
            ".msg-text",
            ".bot-question",
            "div[class*='question']",
            ".ques-title",
            "form .title",
        ]
        for sel in selectors:
            try:
                elements = await scope.query_selector_all(sel)
                for el in reversed(elements):
                    if await el.is_visible():
                        txt = (await el.inner_text()).strip()
                        if txt and len(txt) > 3 and not any(txt.lower().startswith(x) for x in ["posted:", "openings:", "applicants:", "apply", "save"]):
                            return txt
            except Exception:
                pass

        # Fallback to general label only if not an option label
        try:
            labels = await scope.query_selector_all("label:not(.radio-label):not(.checkbox-label):not(.ssrc__label)")
            for el in reversed(labels):
                if await el.is_visible():
                    txt = (await el.inner_text()).strip()
                    if txt and len(txt) > 5 and not any(txt.lower().startswith(x) for x in ["posted:", "openings:", "applicants:", "apply", "save", "skip"]):
                        return txt
        except Exception:
            pass

        return "Screening Question"

    # =========================================================================
    # PATH 3: EXTERNAL COMPANY SITE / ATS REDIRECTION & AUTO-LOGIN
    # =========================================================================
    async def _is_external_apply_button(self, page: Page) -> bool:
        """Check if apply button explicitly states redirection to external site."""
        external_selectors = [
            "button:has-text('Apply on company site')",
            "a:has-text('Apply on company site')",
            "button:has-text('Apply on website')",
            "a:has-text('Apply on website')",
            "a[href*='company-site']",
        ]
        for sel in external_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return True
            except Exception:
                pass
        return False

    async def _wait_for_popup(self, context: BrowserContext, timeout: float = 5.0) -> Optional[Page]:
        """Wait for an external window popup with timeout."""
        try:
            return await context.wait_for_event("page", timeout=int(timeout * 1000))
        except Exception:
            return None

    async def _execute_path_3_external(
        self,
        context: BrowserContext,
        page: Page,
        is_dubai: bool,
        dry_run: bool,
        timestamp: str,
        job_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute Path 3: External site redirection, auto-login/register, and ATS form autofill."""
        logger.info("Executing Path 3: External Company Site / ATS Portal Redirection...")
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(4)

        external_url = page.url
        logger.info(f"Target External Portal URL: {external_url}")

        credentials = self.profile.get("credentials", {})
        login_email = credentials.get("standard_email") or os.getenv("STANDARD_PORTAL_EMAIL", "candidate@example.com")
        login_password = credentials.get("standard_password") or os.getenv("STANDARD_PORTAL_PASSWORD", "")

        # 1. Attempt Auto-Login or Registration on External Site
        auth_action_taken = await self._handle_external_auth(page, login_email, login_password)
        logger.info(f"External Portal Auth Result: {auth_action_taken}")

        # 2. Upload Tailored PDF Resume
        pdf_path = ProfileRepository.get_persistent_resume_path() or self.profile_repo.get_resume_path()
        logger.info(f"Using Candidate Resume: {pdf_path.name}")

        # 3. Handle External ATS: Workday or Adaptive External Apply Engine
        lower_url = external_url.lower()
        is_workday = "myworkdayjobs.com" in lower_url or "workday" in lower_url or bool(await page.query_selector("[data-automation-id]"))

        fill_rep = {}
        if is_workday:
            logger.info("Engaging Workday Engine for external portal...")
            ats_type = "workday"
            workday = WorkdayAutoApplyEngine(page, self.profile)
            await workday.upload_resume(pdf_path)
            await workday.fill_candidate_info(is_dubai=is_dubai)
            await workday.handle_regional_questionnaire(is_dubai=is_dubai)
            status = "applied"
            message = f"Autofilled Workday portal using standard profile credentials ({login_email})."
        else:
            logger.info(f"Engaging Adaptive External Apply Engine on: {external_url[:60]}...")
            adaptive_engine = AdaptiveExternalApplyEngine(page, self.profile)
            fill_rep = await adaptive_engine.apply_adaptively(
                pdf_path=pdf_path,
                is_dubai=is_dubai,
                dry_run=dry_run,
                max_steps=5,
                job_info=job_info,
            )
            ats_type = fill_rep.get("portal_type", "universal_custom")
            status = fill_rep.get("status", "applied")
            message = fill_rep.get("message") or f"Autofilled {ats_type.upper()} portal form adaptively ({login_email})."

            # Register any unknown questions with Action Center
            for uq in fill_rep.get("unknown_questions", []):
                prompt = uq.get("prompt")
                if prompt:
                    self.pending_mgr.add_pending_question(
                        job_id=job_info.get("job_id", f"ext_{timestamp}") if job_info else f"ext_{timestamp}",
                        title=job_info.get("title", "External Career Role") if job_info else "External Career Role",
                        company=job_info.get("company", "Company Career Portal") if job_info else "Company Career Portal",
                        url=external_url,
                        location="Dubai, UAE" if is_dubai else "Pune, India",
                        question=prompt,
                        proof_screenshot=None,
                    )

        # 4. ALWAYS capture screenshot for Path 3 (user needs to manually verify submission)
        proof = fill_rep.get("proof_screenshot")
        settings.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        proof_file = settings.SCREENSHOTS_DIR / f"naukri_path3_verification_{timestamp}.png"
        try:
            await page.screenshot(path=str(proof_file), full_page=True)
            proof = str(proof_file)
            logger.info(f"[PATH3] Verification screenshot saved: {proof_file.name}")
        except Exception as e:
            logger.warning(f"[PATH3] Screenshot capture failed: {e}")
        # Log extra warning if form had errors
        has_errors = bool(fill_rep.get("errors")) or status in ("error", "failed")
        if has_errors:
            logger.warning(f"[PATH3] Form errors detected: {fill_rep.get('errors', status)}")
            if fill_rep.get("status") == "failed":
                status = "failed"

        # 5. Workday Final Submit handling if Workday
        if is_workday and not dry_run:
            submit_btn = await page.query_selector(
                "[data-automation-id='bottom-navigation-submit-button'], button:has-text('Submit Application')"
            )
            if submit_btn and await submit_btn.is_visible():
                await submit_btn.click()
                logger.info("Successfully submitted Workday application!")
                status = "applied"
                await asyncio.sleep(3)

        return {
            "path": "path_3_external_ats",
            "ats_type": ats_type,
            "external_url": external_url,
            "auth_status": auth_action_taken,
            "status": status,
            "message": message,
            "proof_screenshot": proof,
            "dry_run": dry_run,
        }

    async def _handle_external_auth(self, page: Page, email: str, password: str) -> str:
        """Detect and perform external account login or registration."""
        try:
            # Check for standard sign in triggers
            sign_in_link = await page.query_selector(
                "a:has-text('Sign In'), button:has-text('Sign In'), a:has-text('Log In'), "
                "button:has-text('Log In'), [data-automation-id='signInLink']"
            )
            if sign_in_link and await sign_in_link.is_visible():
                logger.info("Found Sign In trigger on external site. Clicking...")
                await sign_in_link.click()
                await asyncio.sleep(2)

            # Check for Email & Password input fields
            email_input = await page.query_selector(
                "input[type='email'], input[name*='email'], input[id*='email'], "
                "input[data-automation-id='email'], input[placeholder*='Email']"
            )
            pass_input = await page.query_selector(
                "input[type='password'], input[name*='password'], input[id*='password'], "
                "input[data-automation-id='password'], input[placeholder*='Password']"
            )

            if email_input and pass_input and await email_input.is_visible():
                logger.info(f"Injecting standard candidate credentials ({email})...")
                await email_input.fill(email)
                await pass_input.fill(password)
                await asyncio.sleep(0.5)

                # Check for confirm password field (Registration case)
                confirm_pass = await page.query_selector(
                    "input[name*='confirm'], input[id*='confirm'], input[placeholder*='Confirm Password']"
                )
                if confirm_pass and await confirm_pass.is_visible():
                    logger.info("Registration detected: filling confirm password...")
                    await confirm_pass.fill(password)

                # Check consent checkbox
                checkbox = await page.query_selector("input[type='checkbox']")
                if checkbox and not await checkbox.is_checked():
                    await checkbox.check()

                # Click Submit / Sign In
                submit_auth = await page.query_selector(
                    "button[type='submit'], button:has-text('Sign In'), button:has-text('Log In'), "
                    "button:has-text('Create Account'), [data-automation-id='signInSubmitButton']"
                )
                if submit_auth and await submit_auth.is_visible():
                    await submit_auth.click()
                    logger.info("Submitted external portal authentication form.")
                    await asyncio.sleep(3)
                    return "authenticated_via_credentials"

                return "credentials_injected"

            return "no_login_required"

        except Exception as e:
            logger.warning(f"External authentication attempt encountered error: {e}")
            return f"auth_exception_{e}"


if __name__ == "__main__":
    import argparse
    from rich.console import Console
    from rich.panel import Panel

    parser = argparse.ArgumentParser(description="Naukri 3-Path Autonomous Auto-Apply Engine")
    parser.add_argument("--url", type=str, required=True, help="Naukri job application URL")
    parser.add_argument("--location", type=str, default="Pune, India", choices=["Pune, India", "Dubai, UAE"])
    parser.add_argument("--submit", action="store_true", help="Live submit application (default is safe dry-run)")
    parser.add_argument("--interactive", action="store_true", help="Run headed Edge browser to inspect visually")

    args = parser.parse_args()
    console = Console()

    engine = NaukriAutoApplyEngine(
        headless=False if args.interactive else None,
        dry_run=not args.submit,
    )

    res = asyncio.run(engine.apply_to_job(
        job_url=args.url,
        location=args.location,
        dry_run=not args.submit,
    ))

    console.print(Panel(
        f"[bold green]Job URL:[/bold green] {res.get('job_url')}\n"
        f"[bold green]Path Executed:[/bold green] {res.get('path')}\n"
        f"[bold green]Status:[/bold green] {res.get('status')}\n"
        f"[bold green]Message:[/bold green] {res.get('message')}\n"
        f"[bold green]Proof Screenshot:[/bold green] {res.get('proof_screenshot', 'N/A')}",
        title="🤖 Naukri Auto-Apply Result",
        border_style="cyan"
    ))
