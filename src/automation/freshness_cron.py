import asyncio
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
import pytz
import httpx

from config.settings import settings
from src.core.logger import logger
from src.automation.browser_factory import BrowserFactory


class AlgorithmicFreshnessCron:
    """Automates daily profile micro-edits on Naukri and Bayt.com to trigger

    algorithmic freshness rankings across India and UAE recruiter searches.
    """

    def __init__(self, headless: Optional[bool] = None, force: bool = False):
        self.headless = settings.HEADLESS if headless is None else headless
        self.force = force
        self.gulf_tz = pytz.timezone(settings.GULF_TIMEZONE)
        self.india_tz = pytz.timezone(settings.DEFAULT_TIMEZONE)

    async def _send_webhook_alert(self, title: str, message: str) -> None:
        """Send execution failure or manual intervention webhook alert."""
        if not settings.FALLBACK_WEBHOOK_URL:
            return
        payload = {
            "content": f"🚨 **Job Hunt Orchestrator Alert** 🚨\n**{title}**\n{message}\n*Time:* {datetime.now().isoformat()}"
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(settings.FALLBACK_WEBHOOK_URL, json=payload)
        except Exception as e:
            logger.warning(f"Failed to deliver fallback webhook notification: {e}")

    def is_gulf_window_active(self) -> bool:
        """Check if current time is within active Gulf recruitment hours (10 AM - 1 PM GST)."""
        if self.force:
            logger.info("Bypassing Gulf recruitment window check (--force flag detected).")
            return True

        now_gst = datetime.now(self.gulf_tz)
        current_hour = now_gst.hour
        is_active = settings.GULF_WINDOW_START_HOUR <= current_hour < settings.GULF_WINDOW_END_HOUR
        logger.info(
            f"Current Gulf Time (GST): {now_gst.strftime('%Y-%m-%d %H:%M:%S %Z')} | "
            f"Active Window: {settings.GULF_WINDOW_START_HOUR}:00 - {settings.GULF_WINDOW_END_HOUR}:00 GST"
        )
        return is_active

    # =========================================================================
    # NAUKRI FRESHNESS IMPLEMENTATION
    # =========================================================================
    async def run_naukri(self) -> bool:
        """Perform micro-edit on Naukri.com profile summary/headline to bump timestamp."""
        logger.info("==================================================")
        logger.info("Starting Naukri Algorithmic Freshness Cycle")
        logger.info("==================================================")

        factory = BrowserFactory(platform_name="naukri", headless=self.headless)
        context, page = await factory.init_session(timezone_id=settings.DEFAULT_TIMEZONE)

        try:
            profile_url = "https://www.naukri.com/mnjuser/profile"
            logger.info(f"Navigating to Naukri Profile: {profile_url}")
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(3)

            page_title = await page.title()
            page_content = await page.content()

            # Detect Akamai or Cloudflare bot blocks
            if "Access Denied" in page_title or "Access Denied" in page_content:
                msg = "Naukri Akamai Access Denied: Session cookies missing or expired. Please run 'login_naukri_sso.bat' or Launch SSO."
                logger.error(msg)
                screenshot_path = settings.LOGS_DIR / "naukri_freshness_error.png"
                await page.screenshot(path=str(screenshot_path))
                await self._send_webhook_alert("Naukri Akamai Block", msg)
                return False

            # Check if login is required
            if "login" in page.url.lower() or "nlogin" in page.url.lower() or await page.query_selector("input#usernameField"):
                logger.warning("Naukri session is unauthenticated. Initiating login sequence...")
                if not settings.NAUKRI_USERNAME or not settings.NAUKRI_PASSWORD:
                    msg = "Naukri credentials missing and no active SSO session. Please run 'login_naukri_sso.bat'."
                    logger.error(msg)
                    await self._send_webhook_alert("Naukri Login Failed", msg)
                    return False

                # Perform login
                await page.fill("input#usernameField", settings.NAUKRI_USERNAME)
                await page.fill("input#passwordField", settings.NAUKRI_PASSWORD)
                await page.click("button[type='submit']")
                logger.info("Submitted login credentials. Waiting for authentication navigation...")

                try:
                    await page.wait_for_url(
                        lambda url: "mnjuser" in url or "homepage" in url or "profile" in url,
                        timeout=30000,
                    )
                except Exception:
                    # Check if OTP / Captcha screen triggered
                    if await page.query_selector("input[placeholder*='OTP'], .otp-container, iframe[src*='recaptcha']"):
                        err_msg = "Naukri requires OTP or CAPTCHA resolution. Re-run with --interactive to authenticate."
                        logger.error(err_msg)
                        screenshot_path = settings.LOGS_DIR / "naukri_otp_blocked.png"
                        await page.screenshot(path=str(screenshot_path))
                        await self._send_webhook_alert("Naukri 2FA/CAPTCHA Block", err_msg)
                        return False

                await factory.save_session_state()
                logger.info("Login successful. Navigating back to profile page...")
                await page.goto(profile_url, wait_until="domcontentloaded")
                await asyncio.sleep(3)

            # Verify profile loaded
            await page.wait_for_selector(".profile-summary, .widgetHead, .resumeHeadline, .profileSummary", timeout=30000)
            logger.info("Successfully navigated to Naukri Profile dashboard.")

            # Micro-edit Strategy: Edit Resume Headline
            headline_edit_selectors = [
                "div[widget-id='resumeHeadline'] .edit",
                ".resumeHeadline .edit",
                "span:text-matches('Resume Headline', 'i') ~ span.edit",
                "//div[contains(@class, 'resumeHeadline')]//span[contains(@class, 'edit')]",
            ]

            edit_btn = None
            for selector in headline_edit_selectors:
                if selector.startswith("//"):
                    edit_btn = await page.query_selector(f"xpath={selector}")
                else:
                    edit_btn = await page.query_selector(selector)
                if edit_btn and await edit_btn.is_visible():
                    logger.info(f"Located Resume Headline edit trigger with: {selector}")
                    break

            if edit_btn:
                await edit_btn.click()
                await asyncio.sleep(1.5)

                textarea = await page.wait_for_selector(
                    "textarea#resumeHeadline, textarea.ge__text-area, textarea#resumeHeadlineTxt, textarea[name='resumeHeadline'], textarea",
                    timeout=10000,
                )
                current_text = await textarea.input_value()
                logger.info(f"Current Resume Headline length: {len(current_text)} characters. Value: '{current_text[:30]}...'")

                # Micro-edit: Toggle trailing space
                if current_text.endswith(" "):
                    new_text = current_text.rstrip()
                else:
                    new_text = current_text + " "

                await textarea.fill(new_text)
                await asyncio.sleep(1)

                # Explicitly locate the visible headline form save button, filtering out hidden photo modal buttons
                save_btn = None
                candidate_selectors = [
                    "form[name='resumeHeadlineForm'] button[type='submit']",
                    "form[name='resumeHeadlineForm'] button",
                    "button.btn-dark-ot:not(.save-photo):has-text('Save')",
                    ".resumeHeadline button.btn-dark-ot:not(.save-photo)",
                    ".action button.btn-dark-ot:has-text('Save')",
                ]
                for c_sel in candidate_selectors:
                    try:
                        cand = await page.wait_for_selector(c_sel, timeout=3000)
                        if cand and await cand.is_visible():
                            save_btn = cand
                            logger.info(f"Found visible headline Save button with: {c_sel}")
                            break
                    except Exception:
                        continue

                if not save_btn:
                    all_saves = await page.query_selector_all("button:has-text('Save')")
                    for b in all_saves:
                        cls = (await b.get_attribute("class")) or ""
                        txt = (await b.inner_text()) or ""
                        if await b.is_visible() and "photo" not in cls.lower() and "photo" not in txt.lower():
                            save_btn = b
                            logger.info("Found visible headline Save button via text fallback.")
                            break

                if save_btn:
                    await save_btn.click()
                    logger.info("Saved micro-edited Resume Headline to Naukri.")
                    await asyncio.sleep(2)
                else:
                    raise RuntimeError("Could not find visible Save button for Resume Headline.")
            else:
                # Fallback to Profile Summary micro-edit
                logger.info("Headline edit button not found; attempting Profile Summary micro-edit...")
                summary_edit = await page.wait_for_selector(
                    "div[widget-id='profileSummary'] .edit, .profileSummary .edit",
                    timeout=15000,
                )
                await summary_edit.click()
                await asyncio.sleep(1.5)

                textarea = await page.wait_for_selector(
                    "textarea#profileSummaryTxt, textarea",
                    timeout=10000,
                )
                current_text = await textarea.input_value()
                new_text = current_text[:-1] if current_text.endswith(".") else current_text + "."
                await textarea.fill(new_text)
                await asyncio.sleep(1)

                save_btn = await page.wait_for_selector("button:has-text('Save')", timeout=10000)
                await save_btn.click()
                logger.info("Saved micro-edited Profile Summary to Naukri.")
                await asyncio.sleep(2)

            # Persist fresh session state
            await factory.save_session_state()

            # Screenshot confirmation
            screenshot_path = settings.LOGS_DIR / "naukri_freshness_success.png"
            await page.screenshot(path=str(screenshot_path))
            logger.info(f"Naukri freshness bump succeeded. Proof captured: {screenshot_path}")
            return True

        except Exception as e:
            logger.error(f"Error during Naukri algorithmic freshness execution: {e}")
            screenshot_path = settings.LOGS_DIR / "naukri_freshness_error.png"
            try:
                await page.screenshot(path=str(screenshot_path))
            except Exception:
                pass
            await self._send_webhook_alert("Naukri Freshness Failure", str(e))
            return False
        finally:
            await factory.close()

    # =========================================================================
    # BAYT.COM FRESHNESS IMPLEMENTATION (UAE / GULF FOCUS)
    # =========================================================================
    async def run_bayt(self) -> bool:
        """Perform micro-edit on Bayt.com CV/Profile during active Gulf hours (10:00 - 13:00 GST)."""
        logger.info("==================================================")
        logger.info("Starting Bayt.com Algorithmic Freshness Cycle")
        logger.info("==================================================")

        if not self.is_gulf_window_active():
            logger.warning(
                "Skipping Bayt.com run: Current time is outside Gulf recruiter peak activity hours (10:00 - 13:00 GST). "
                "Use --force to override."
            )
            return False

        factory = BrowserFactory(platform_name="bayt", headless=self.headless)
        context, page = await factory.init_session(timezone_id=settings.GULF_TIMEZONE)

        try:
            cv_url = "https://www.bayt.com/en/my-cv/"
            logger.info(f"Navigating to Bayt Profile: {cv_url}")
            await page.goto(cv_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(3)

            page_title = await page.title()
            if "Just a moment..." in page_title or "challenge" in page.url.lower():
                msg = "Bayt Cloudflare challenge encountered: Session missing or expired. Please run 'login_bayt_sso.bat'."
                logger.error(msg)
                screenshot_path = settings.LOGS_DIR / "bayt_freshness_error.png"
                await page.screenshot(path=str(screenshot_path))
                await self._send_webhook_alert("Bayt Cloudflare Block", msg)
                return False

            # Authentication verification
            if "login" in page.url.lower() or await page.query_selector("input[name='login_email'], input#LoginForm_username"):
                logger.warning("Bayt session unauthenticated. Checking credentials or SSO...")
                if not settings.BAYT_USERNAME or not settings.BAYT_PASSWORD:
                    msg = "Bayt session is unauthenticated and credentials are not provided. Please run 'login_bayt_sso.bat'."
                    logger.error(msg)
                    await self._send_webhook_alert("Bayt Login Failed", msg)
                    return False

                # Handle Bayt login form
                user_input = await page.wait_for_selector(
                    "input[name='login_email'], input#LoginForm_username, input[type='email']",
                    timeout=15000,
                )
                await user_input.fill(settings.BAYT_USERNAME)

                pwd_input = await page.wait_for_selector(
                    "input[name='login_password'], input#LoginForm_password, input[type='password']",
                    timeout=15000,
                )
                await pwd_input.fill(settings.BAYT_PASSWORD)

                submit_btn = await page.wait_for_selector(
                    "button#login-button, button[type='submit'], input[type='submit']",
                    timeout=10000,
                )
                await submit_btn.click()
                logger.info("Submitted Bayt credentials. Awaiting landing page...")

                try:
                    await page.wait_for_url(
                        lambda url: "my-profile" in url or "my-cv" in url or "home" in url,
                        timeout=30000,
                    )
                except Exception:
                    if await page.query_selector(".g-recaptcha, iframe[src*='recaptcha'], input[name*='captcha']"):
                        err_msg = "Bayt CAPTCHA challenge detected. Re-run with --interactive to solve manually."
                        logger.error(err_msg)
                        screenshot_path = settings.LOGS_DIR / "bayt_captcha_blocked.png"
                        await page.screenshot(path=str(screenshot_path))
                        await self._send_webhook_alert("Bayt CAPTCHA Block", err_msg)
                        return False

                await factory.save_session_state()
                logger.info("Bayt authentication successful.")
                await page.goto(cv_url, wait_until="domcontentloaded")
                await asyncio.sleep(3)

            # Locate CV summary / Objective micro-edit trigger
            logger.info("Locating CV Summary or Objective section on Bayt...")
            summary_edit_btn = await page.query_selector(
                "a[data-js-cv-section='summary'], a.edit-summary, a:has-text('Edit Summary'), [data-action='edit-summary']"
            )

            if not summary_edit_btn:
                # Try finding edit icon near Summary/Headline
                summary_edit_btn = await page.query_selector(
                    "section#cv-summary a.is-editable, .t-summary a.edit, a:has-text('Edit Headline')"
                )

            edit_performed = False
            if summary_edit_btn and await summary_edit_btn.is_visible():
                await summary_edit_btn.click()
                await asyncio.sleep(1.5)

                textarea = await page.wait_for_selector(
                    "textarea[name*='summary'], textarea#cv_summary, textarea",
                    timeout=10000,
                )
                current_text = await textarea.input_value()
                logger.info(f"Current Bayt Summary length: {len(current_text)} characters.")

                new_text = current_text[:-1] if current_text.endswith(" ") else current_text + " "
                await textarea.fill(new_text)
                await asyncio.sleep(1)

                save_btn = await page.wait_for_selector(
                    "button[type='submit']:has-text('Save'), button:has-text('Save'), a:has-text('Save')",
                    timeout=10000,
                )
                await save_btn.click()
                logger.info("Saved micro-edited summary on Bayt.com.")
                await asyncio.sleep(2)
                edit_performed = True
            else:
                logger.warning("Summary edit button not found directly; touching CV refresh anchor...")
                refresh_anchor = await page.query_selector("a:has-text('Update CV'), a:has-text('Refresh')")
                if refresh_anchor and await refresh_anchor.is_visible():
                    await refresh_anchor.click()
                    await asyncio.sleep(2)
                    edit_performed = True

            if not edit_performed:
                msg = "Bayt CV edit triggers not found. Verify you are authenticated to Bayt via 'login_bayt_sso.bat'."
                logger.error(msg)
                screenshot_path = settings.LOGS_DIR / "bayt_freshness_error.png"
                await page.screenshot(path=str(screenshot_path))
                return False

            await factory.save_session_state()

            screenshot_path = settings.LOGS_DIR / "bayt_freshness_success.png"
            await page.screenshot(path=str(screenshot_path))
            logger.info(f"Bayt freshness bump succeeded. Proof captured: {screenshot_path}")
            return True

        except Exception as e:
            logger.error(f"Error during Bayt algorithmic freshness execution: {e}")
            screenshot_path = settings.LOGS_DIR / "bayt_freshness_error.png"
            try:
                await page.screenshot(path=str(screenshot_path))
            except Exception:
                pass
            await self._send_webhook_alert("Bayt Freshness Failure", str(e))
            return False
        finally:
            await factory.close()

    # =========================================================================
    # DAEMON SCHEDULER
    # =========================================================================
    async def run_daemon(self) -> None:
        """Run an automated continuous daemon triggering Naukri at 09:00 IST and Bayt at 10:30 GST daily."""
        import schedule

        logger.info("Starting Freshness Daemon scheduler...")
        logger.info("Scheduled jobs: Naukri daily at 09:00 IST | Bayt daily at 10:30 GST")

        def trigger_naukri():
            logger.info("Cron trigger: Running Naukri freshness job.")
            asyncio.run(self.run_naukri())

        def trigger_bayt():
            logger.info("Cron trigger: Running Bayt freshness job (Gulf peak window).")
            asyncio.run(self.run_bayt())

        schedule.every().day.at("09:00").do(trigger_naukri)
        # 10:30 GST is 12:00 IST
        schedule.every().day.at("12:00").do(trigger_bayt)

        while True:
            schedule.run_pending()
            await asyncio.sleep(30)


async def bootstrap_interactive_session(platform_name: str, timeout_seconds: int = 300) -> dict:
    """Launch visible browser for user to authenticate via SSO (Google/LinkedIn) or credentials.

    Captures session storage state cookies and extracts candidate profile metadata.
    """
    from src.automation.sso_manager import SSOManager
    mgr = SSOManager(platform_name)
    return await mgr.bootstrap_sso_session(timeout_seconds=timeout_seconds)


# =============================================================================
# CLI ENTRY POINT
# =============================================================================
async def main():
    parser = argparse.ArgumentParser(
        description="Job Hunt Orchestrator - Algorithmic Freshness Cron Job (Naukri & Bayt)"
    )
    parser.add_argument(
        "--platform",
        choices=["naukri", "bayt", "all"],
        default="naukri",
        help="Target platform to run the freshness bump on (default: naukri)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run browser in visible (headed) mode for initial manual login, OTP, or CAPTCHA resolution.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass Gulf timezone window checks for Bayt.com.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run in persistent daemon mode with scheduled daily triggers.",
    )

    args = parser.parse_args()

    # If running interactively, force headless=False
    headless = False if args.interactive else None
    runner = AlgorithmicFreshnessCron(headless=headless, force=args.force)

    if args.daemon:
        await runner.run_daemon()
        return

    success_naukri = True
    success_bayt = True

    if args.platform in ("naukri", "all"):
        success_naukri = await runner.run_naukri()

    if args.platform in ("bayt", "all"):
        success_bayt = await runner.run_bayt()

    if not (success_naukri and success_bayt):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
