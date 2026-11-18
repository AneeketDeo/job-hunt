import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from config.settings import settings
from src.core.logger import logger
from src.automation.browser_factory import CHROME_LAUNCH_ARGS, STEALTH_EVASION_SCRIPT


def _is_valid_candidate_name(name: str) -> bool:
    """Validate that scraped text is a real candidate name and not a marketing slogan or banner."""
    if not name or len(name) < 2 or len(name) > 60:
        return False
    lower = name.lower()
    invalid_phrases = [
        "login", "sign in", "sign up", "register", "ready to take", "next step",
        "welcome", "dashboard", "profile", "account", "feed", "search", "notification",
        "home", "message", "candidate", "unknown", "find job", "get ahead",
        "fast forward", "looking for", "upgrade", "verify", "premium"
    ]
    if any(phrase in lower for phrase in invalid_phrases):
        return False
    if "?" in name or "!" in name:
        return False
    return True


class SSOManager:
    """Manages SSO Authentication (Google/LinkedIn/Apple), session persistence,

    and credential-free automation integration for Naukri and Bayt.com.
    """

    PLATFORM_CONFIGS = {
        "naukri": {
            "name": "Naukri",
            "login_url": "https://www.naukri.com/nlogin/login",
            "profile_url": "https://www.naukri.com/mnjuser/profile",
            "success_url_patterns": ["mnjuser", "homepage", "mynaukri", "profile"],
            "auth_cookies": ["naukri_auth", "isLoggedIn", "nlogin", "PROFILE_DETAILS", "EMP_ID"],
            "name_selectors": [
                ".user-name",
                ".name-title",
                "div.name",
                "h1.user-name",
                ".profile-name",
                "[widget-id='header'] .name",
                ".user-info .name",
            ],
            "email_selectors": [
                ".user-email",
                ".email",
                "[widget-id='header'] .email",
                ".user-info .email",
            ],
            "title_selectors": [
                ".resumeHeadlineTxt",
                ".user-desig",
                ".designation",
                "[widget-id='header'] .designation",
            ],
        },
        "bayt": {
            "name": "Bayt.com",
            "login_url": "https://www.bayt.com/en/login/",
            "profile_url": "https://www.bayt.com/en/my-cv/",
            "success_url_patterns": ["my-cv", "dashboard", "inbox", "workspace", "profile", "home"],
            "auth_cookies": ["bayt_user", "b_auth", "PHPSESSID", "_bayt_token", "bayt_session", "bayt_logged_in"],
            "name_selectors": [
                ".u-name",
                "h1.t-large",
                ".user-details h1",
                "[data-automation-id='user-name']",
                ".user-menu-name",
                "h1",
            ],
            "email_selectors": [
                ".u-email",
                ".user-details .email",
                "[data-automation-id='user-email']",
            ],
            "title_selectors": [
                ".u-title",
                ".headline",
                "[data-automation-id='user-title']",
            ],
        },
        "indeed": {
            "name": "Indeed India",
            "login_url": "https://secure.indeed.com/account/login",
            "profile_url": "https://profile.indeed.com",
            "success_url_patterns": ["profile.indeed.com", "myjobs", "account", "home", "resume", "feed"],
            "auth_cookies": ["SURF", "PREF", "SHOE", "CTK", "JSESSIONID", "INDEED_CSRF_TOKEN", "DDC", "SOCK"],
            "name_selectors": [
                "[data-testid='profile-name']",
                ".profile-name",
                "h1",
                ".gnav-AccountMenu",
                ".user-name",
            ],
            "email_selectors": [
                "[data-testid='profile-email']",
                ".profile-email",
                "input[type='email']",
                ".user-email",
            ],
            "title_selectors": [
                "[data-testid='profile-title']",
                ".headline",
                "h2",
            ],
        },
        "cutshort": {
            "name": "Cutshort",
            "login_url": "https://cutshort.io/login",
            "profile_url": "https://cutshort.io/profile",
            "success_url_patterns": ["profile", "my-applications", "dashboard", "matches"],
            "auth_cookies": ["cutshort_token", "authToken", "token", "session", "user_id", "connect.sid"],
            "name_selectors": [
                ".profile-name",
                ".user-name",
                "h1",
                "h2",
                "[data-testid='userName']",
            ],
            "email_selectors": [
                ".user-email",
                ".profile-email",
                "input[type='email']",
            ],
            "title_selectors": [
                ".designation",
                ".profile-headline",
                "p.title",
            ],
        },
        "wellfound": {
            "name": "Wellfound (AngelList)",
            "login_url": "https://wellfound.com/login",
            "profile_url": "https://wellfound.com/profile/edit",
            "success_url_patterns": ["profile", "jobs", "overview", "dashboard", "applications"],
            "auth_cookies": ["_angellist", "remember_user_token", "ajs_user_id", "wellfound_session", "_session_id"],
            "name_selectors": [
                "[data-test='userName']",
                "h1",
                ".profile-name",
                "input[name='user[name]']",
            ],
            "email_selectors": [
                "input[name='user[email]']",
                ".user-email",
                ".profile-email",
            ],
            "title_selectors": [
                "input[name='user[bio]']",
                "input[name='user[role]']",
                ".user-headline",
            ],
        },
    }

    def __init__(self, platform_name: str):
        self.platform = platform_name.lower()
        if self.platform not in self.PLATFORM_CONFIGS:
            raise ValueError(f"Unsupported platform: {self.platform}. Supported: {list(self.PLATFORM_CONFIGS.keys())}")
        self.config = self.PLATFORM_CONFIGS[self.platform]
        self.state_file: Path = settings.SESSIONS_DIR / f"{self.platform}_state.json"
        self.info_file: Path = settings.SESSIONS_DIR / f"{self.platform}_sso_info.json"

    def has_session(self) -> bool:
        """Check if saved session state file exists and has content."""
        if not self.state_file.exists():
            return False
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return bool(data.get("cookies") or data.get("origins"))
        except Exception:
            return False

    def get_sso_info(self) -> Dict[str, Any]:
        """Load saved SSO profile and metadata."""
        if self.info_file.exists():
            try:
                with open(self.info_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "platform": self.platform,
            "platform_name": self.config["name"],
            "authenticated": self.has_session(),
            "candidate_name": "Unknown",
            "email": "",
            "auth_type": "SSO",
            "last_login": None,
            "cookies_count": 0,
        }

    def save_sso_info(self, info: Dict[str, Any]) -> None:
        """Persist SSO profile and metadata to disk."""
        self.info_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.info_file, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)

    async def bootstrap_sso_session(self, timeout_seconds: int = 300) -> Dict[str, Any]:
        """Launch visible headed browser specifically tailored for SSO authentication.

        Configured with Microsoft Edge / Chrome anti-detection so Google Identity
        and LinkedIn SSO allow seamless sign-in without 'insecure browser' blocks.
        """
        logger.info(f"==================================================")
        logger.info(f"Launching SSO Interactive Login for [{self.config['name']}]")
        logger.info(f"Login URL: {self.config['login_url']}")
        logger.info(f"Timeout: {timeout_seconds}s (Complete sign-in with Google/LinkedIn)")
        logger.info(f"==================================================")

        playwright = await async_playwright().start()
        browser: Optional[Browser] = None
        context: Optional[BrowserContext] = None

        try:
            # Prefer msedge on Windows to evade Google Identity automation blocks
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ]
            try:
                browser = await playwright.chromium.launch(
                    channel="msedge",
                    headless=False,
                    args=launch_args,
                    ignore_default_args=["--enable-automation"],
                )
                logger.info("Launched Microsoft Edge for authentic SSO login.")
            except Exception as e:
                logger.warning(f"Could not launch Edge ({e}). Falling back to Chromium.")
                browser = await playwright.chromium.launch(
                    headless=False,
                    args=launch_args,
                    ignore_default_args=["--enable-automation"],
                )

            context_kwargs = {
                "viewport": None,  # Match maximized window
                "user_agent": settings.DEFAULT_USER_AGENT,
                "locale": "en-US",
                "timezone_id": settings.DEFAULT_TIMEZONE,
                "color_scheme": "light",
            }

            # Restore existing session if present so user doesn't lose other cookies
            if self.has_session():
                context_kwargs["storage_state"] = str(self.state_file)

            context = await browser.new_context(**context_kwargs)
            await context.add_init_script(STEALTH_EVASION_SCRIPT)

            # Listen for popup tabs (essential for Google/LinkedIn OAuth popups)
            tracked_pages: List[Page] = []

            def handle_new_page(new_page: Page):
                logger.info(f"SSO Popup/Tab opened: {new_page.url}")
                tracked_pages.append(new_page)

            context.on("page", handle_new_page)

            page = await context.new_page()
            tracked_pages.append(page)
            page.set_default_timeout(60000)

            await page.goto(self.config["login_url"], wait_until="domcontentloaded")

            logger.info(f"Browser open! Please click 'Sign in with Google' or your SSO provider in the browser.")

            # Monitor all open pages and cookies until authenticated
            start_time = asyncio.get_event_loop().time()
            authenticated = False
            active_auth_page: Optional[Page] = page

            while asyncio.get_event_loop().time() - start_time < timeout_seconds:
                # 1. Check URL patterns across all tracked pages
                for p in list(tracked_pages):
                    try:
                        curr_url = p.url.lower()
                        # If reached an authenticated area
                        if any(pat in curr_url for pat in self.config["success_url_patterns"]):
                            if not any(deny in curr_url for deny in ["login", "oauth2", "accounts.google.com"]):
                                logger.info(f"Authenticated landing detected on URL: {curr_url}")
                                authenticated = True
                                active_auth_page = p
                                break
                    except Exception:
                        pass

                if authenticated:
                    break

                # 2. Check cookies across context
                try:
                    cookies = await context.cookies()
                    cookie_names = [c["name"] for c in cookies]
                    for auth_cookie in self.config["auth_cookies"]:
                        if auth_cookie in cookie_names:
                            logger.info(f"Detected core authentication cookie: {auth_cookie}")
                            authenticated = True
                            break
                except Exception:
                    pass

                if authenticated:
                    break

                await asyncio.sleep(2)

            if not authenticated:
                logger.warning(f"SSO login timed out after {timeout_seconds} seconds.")
                return {
                    "status": "timeout",
                    "platform": self.platform,
                    "message": f"SSO Login was not completed within {timeout_seconds} seconds.",
                }

            # Settle navigation and tokens
            await asyncio.sleep(3)

            # Save full Playwright storage state (cookies + localStorage origins)
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(self.state_file))
            logger.info(f"Persisted SSO authentication state to: {self.state_file}")

            # Extract Candidate Metadata from authenticated session
            candidate_name = "Candidate"
            candidate_email = ""
            candidate_title = ""

            try:
                # Navigate to profile page if not already there to inspect DOM
                if self.config["profile_url"] not in active_auth_page.url:
                    logger.info(f"Navigating to profile to read SSO candidate data: {self.config['profile_url']}")
                    await active_auth_page.goto(self.config["profile_url"], wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(2)

                # Scrape Name
                for sel in self.config["name_selectors"]:
                    try:
                        el = await active_auth_page.query_selector(sel)
                        if el:
                            txt = (await el.text_content() or "").strip()
                            if _is_valid_candidate_name(txt):
                                candidate_name = txt
                                logger.info(f"Extracted candidate name from SSO profile: {candidate_name}")
                                break
                    except Exception:
                        pass

                # Scrape Email
                for sel in self.config["email_selectors"]:
                    try:
                        el = await active_auth_page.query_selector(sel)
                        if el:
                            txt = (await el.text_content() or "").strip()
                            if "@" in txt:
                                candidate_email = txt
                                logger.info(f"Extracted email from SSO profile: {candidate_email}")
                                break
                    except Exception:
                        pass

                # Scrape Headline / Title
                for sel in self.config["title_selectors"]:
                    try:
                        el = await active_auth_page.query_selector(sel)
                        if el:
                            txt = (await el.text_content() or "").strip()
                            if txt:
                                candidate_title = txt
                                break
                    except Exception:
                        pass

            except Exception as e:
                logger.warning(f"Could not scrape complete profile data (non-fatal): {e}")

            # Persist SSO info
            cookies = await context.cookies()
            sso_data = {
                "platform": self.platform,
                "platform_name": self.config["name"],
                "authenticated": True,
                "auth_type": "SSO (Google / LinkedIn)",
                "candidate_name": candidate_name,
                "email": candidate_email or settings.GMAIL_USER_EMAIL,
                "title": candidate_title,
                "cookies_count": len(cookies),
                "last_authenticated_at": datetime.now().isoformat(),
                "state_file": str(self.state_file),
            }
            self.save_sso_info(sso_data)

            # Sync to base profile_data.json if candidate name was extracted
            if candidate_name and candidate_name != "Candidate":
                try:
                    p_file = settings.DATA_DIR / "profile_data.json"
                    if p_file.exists():
                        with open(p_file, "r", encoding="utf-8") as f:
                            p_data = json.load(f)
                        p_data.setdefault("personal", {})
                        if candidate_name:
                            p_data["personal"]["name"] = candidate_name
                        if candidate_email:
                            p_data["personal"]["email"] = candidate_email
                        with open(p_file, "w", encoding="utf-8") as f:
                            json.dump(p_data, f, indent=2)
                        logger.info("Updated profile_data.json personal information from SSO login.")
                except Exception as e:
                    logger.warning(f"Could not update profile_data.json: {e}")

            logger.info(f"SSO login successfully captured for [{self.config['name']}] as '{candidate_name}'!")
            return {
                "status": "success",
                "platform": self.platform,
                "sso_info": sso_data,
                "message": f"Successfully authenticated to {self.config['name']} via SSO as {candidate_name}!",
            }

        except Exception as e:
            logger.error(f"Error during SSO login: {e}")
            return {"status": "error", "platform": self.platform, "message": str(e)}
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            try:
                await playwright.stop()
            except Exception:
                pass

    async def verify_session(self) -> Dict[str, Any]:
        """Headlessly test if the saved SSO session is still active and valid.

        Navigates to the user's profile/dashboard with saved storageState.
        """
        if not self.has_session():
            return {
                "platform": self.platform,
                "authenticated": False,
                "status": "missing",
                "message": f"No saved session for {self.config['name']}. Please launch SSO Login.",
            }

        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch(
                headless=True,
                args=CHROME_LAUNCH_ARGS,
            )
            context = await browser.new_context(
                storage_state=str(self.state_file),
                user_agent=settings.DEFAULT_USER_AGENT,
            )
            await context.add_init_script(STEALTH_EVASION_SCRIPT)
            page = await context.new_page()

            logger.info(f"Verifying SSO session for [{self.config['name']}] at {self.config['profile_url']}...")
            await page.goto(self.config["profile_url"], wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2)

            curr_url = page.url.lower()
            is_valid = not ("login" in curr_url or "nlogin" in curr_url)

            # Update info
            current_info = self.get_sso_info()
            current_info["authenticated"] = is_valid
            current_info["last_verified_at"] = datetime.now().isoformat()

            if is_valid:
                # Capture verification screenshot
                settings.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                proof_file = settings.SCREENSHOTS_DIR / f"{self.platform}_sso_verified_{timestamp}.png"
                try:
                    await page.screenshot(path=str(proof_file), full_page=True)
                    current_info["proof_screenshot"] = str(proof_file)
                    logger.info(f"SSO verification screenshot saved to: {proof_file}")
                except Exception as sc_err:
                    logger.debug(f"Failed to capture SSO verification screenshot: {sc_err}")

                # Try to re-extract name if unknown or invalid
                curr_name = current_info.get("candidate_name", "")
                if not _is_valid_candidate_name(curr_name):
                    for sel in self.config["name_selectors"]:
                        try:
                            el = await page.query_selector(sel)
                            if el:
                                txt = (await el.text_content() or "").strip()
                                if _is_valid_candidate_name(txt):
                                    current_info["candidate_name"] = txt
                                    break
                        except Exception:
                            pass

            self.save_sso_info(current_info)
            await browser.close()

            return {
                "platform": self.platform,
                "authenticated": is_valid,
                "status": "valid" if is_valid else "expired",
                "sso_info": current_info,
                "message": f"SSO session is {'ACTIVE and VALID' if is_valid else 'EXPIRED. Please re-authenticate'}.",
            }

        except Exception as e:
            logger.error(f"Error verifying session for {self.platform}: {e}")
            return {
                "platform": self.platform,
                "authenticated": False,
                "status": "error",
                "message": f"Verification error: {e}",
            }
        finally:
            try:
                await playwright.stop()
            except Exception:
                pass

    def import_session_json(self, raw_data: str) -> Dict[str, Any]:
        """Import session state or raw cookies from JSON string.

        Supports Playwright storageState format or list of cookie dictionaries.
        """
        try:
            parsed = json.loads(raw_data)
            normalized_state: Dict[str, Any] = {"cookies": [], "origins": []}

            if isinstance(parsed, dict) and ("cookies" in parsed or "origins" in parsed):
                normalized_state = parsed
            elif isinstance(parsed, list):
                # Standard cookie list (EditThisCookie / Cookie-Editor)
                normalized_state["cookies"] = parsed
            else:
                return {"status": "error", "message": "Invalid JSON format. Expected object with 'cookies' or list of cookies."}

            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(normalized_state, f, indent=2)

            info = self.get_sso_info()
            info["authenticated"] = True
            info["auth_type"] = "Imported SSO State"
            info["cookies_count"] = len(normalized_state.get("cookies", []))
            info["last_authenticated_at"] = datetime.now().isoformat()
            self.save_sso_info(info)

            logger.info(f"Successfully imported {len(normalized_state.get('cookies', []))} cookies for [{self.platform}].")
            return {
                "status": "success",
                "platform": self.platform,
                "cookies_count": len(normalized_state.get("cookies", [])),
                "message": f"Successfully imported session for {self.config['name']}.",
            }
        except Exception as e:
            logger.error(f"Failed to import session JSON: {e}")
            return {"status": "error", "message": f"Failed to parse JSON: {e}"}


if __name__ == "__main__":
    import argparse
    import sys

    # Enforce Proactor loop on Windows for Playwright
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    parser = argparse.ArgumentParser(description="Interactive SSO Session Launcher for Multi-Portal Job Platforms")
    parser.add_argument("--platform", choices=["naukri", "bayt", "indeed", "cutshort", "wellfound"], default="naukri", help="Target platform for SSO login")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds")
    args = parser.parse_args()

    mgr = SSOManager(args.platform)
    print("\n" + "=" * 60)
    print(f"  LAUNCHING INTERACTIVE SSO LOGIN: {args.platform.upper()}")
    print("=" * 60)
    print("A Microsoft Edge browser window will open shortly in the foreground.")
    print("1. Click 'Sign in with Google' or your SSO provider in the browser.")
    print("2. Complete your sign-in / 2FA.")
    print("3. As soon as you land on your profile, cookies and candidate info")
    print("   will be automatically extracted and saved for all automations.")
    print(f"Waiting for your login (timeout: {args.timeout}s)...\n")

    res = asyncio.run(mgr.bootstrap_sso_session(timeout_seconds=args.timeout))
    print("\n" + "=" * 60)
    print(f"RESULT: {res.get('status', 'unknown').upper()}")
    print(f"MESSAGE: {res.get('message', '')}")
    if res.get("status") == "success":
        info = res.get("sso_info", {})
        print(f"Candidate: {info.get('candidate_name', 'Authenticated User')}")
        print(f"Email: {info.get('email', 'N/A')}")
        print(f"Cookies Saved: {info.get('cookies_count', 0)}")
    print("=" * 60)
    input("\nPress Enter to exit...")
