import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional, Tuple
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)
from config.settings import settings
from src.core.logger import logger

STEALTH_EVASION_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined
});
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {}
};
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5]
});
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en']
});
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);
"""

CHROME_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-accelerated-2d-canvas",
    "--disable-gpu",
    "--window-size=1920,1080",
    "--start-maximized",
]


def run_playwright_in_proactor_loop(coro_fn: Callable[..., Coroutine[Any, Any, Any]], *args, **kwargs) -> Any:
    """Run an async Playwright coroutine inside a dedicated thread with a ProactorEventLoop on Windows.

    Guarantees that asyncio.create_subprocess_exec never raises NotImplementedError.
    """
    if sys.platform != "win32":
        return asyncio.run(coro_fn(*args, **kwargs))

    p_loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(p_loop)
    try:
        return p_loop.run_until_complete(coro_fn(*args, **kwargs))
    finally:
        p_loop.close()


async def safe_playwright_exec(coro_fn: Callable[..., Coroutine[Any, Any, Any]], *args, **kwargs) -> Any:
    """Safely execute Playwright asynchronous operations from any event loop (including Uvicorn/ASGI)."""
    return await asyncio.to_thread(run_playwright_in_proactor_loop, coro_fn, *args, **kwargs)


class BrowserFactory:
    """Production-grade Playwright browser factory with stealth and session persistence."""

    def __init__(self, platform_name: str, headless: Optional[bool] = None):
        self.platform_name = platform_name.lower()
        self.headless = settings.HEADLESS if headless is None else headless
        self.state_file: Path = settings.SESSIONS_DIR / f"{self.platform_name}_state.json"
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    def has_saved_session(self) -> bool:
        """Check if a non-empty session state file exists."""
        if not self.state_file.exists():
            return False
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return bool(data.get("cookies") or data.get("origins"))
        except Exception:
            return False

    async def init_session(
        self, timezone_id: Optional[str] = None
    ) -> Tuple[BrowserContext, Page]:
        """Launch browser, apply stealth evasion, and restore session if available."""
        self._playwright = await async_playwright().start()

        logger.debug(
            f"Launching Chromium/Edge for [{self.platform_name}] (headless={self.headless})"
        )
        launch_kwargs: Dict[str, Any] = {
            "headless": self.headless,
            "slow_mo": settings.SLOW_MO,
            "args": CHROME_LAUNCH_ARGS,
        }
        if sys.platform == "win32":
            # Use Microsoft Edge on Windows to evade Akamai / Cloudflare bot blocks
            launch_kwargs["channel"] = "msedge"
            launch_kwargs["ignore_default_args"] = ["--enable-automation"]

        try:
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        except Exception as e:
            logger.warning(f"Edge launch failed ({e}). Falling back to default Chromium.")
            launch_kwargs.pop("channel", None)
            launch_kwargs.pop("ignore_default_args", None)
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)

        context_kwargs = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": settings.DEFAULT_USER_AGENT,
            "locale": "en-US",
            "timezone_id": timezone_id or settings.DEFAULT_TIMEZONE,
            "color_scheme": "light",
            "accept_downloads": True,
        }

        if self.has_saved_session():
            logger.info(f"Restoring saved session from: {self.state_file}")
            context_kwargs["storage_state"] = str(self.state_file)
        else:
            logger.warning(
                f"No saved session for [{self.platform_name}]. Initializing fresh context."
            )

        self._context = await self._browser.new_context(**context_kwargs)

        # Inject stealth scripts into every frame before execution
        await self._context.add_init_script(STEALTH_EVASION_SCRIPT)

        # Optimize compute & network: abort heavy third-party analytics, ad trackers and beacons
        if self.headless:
            async def _filter_routes(route):
                r_url = route.request.url.lower()
                trackers = (
                    "google-analytics.com",
                    "googletagmanager.com",
                    "doubleclick.net",
                    "branch.io",
                    "clarity.ms",
                    "newrelic.com",
                    "hotjar.com",
                    "adroll.com",
                    "scorecardresearch.com",
                    "connect.facebook.net",
                    "criteo.com",
                )
                if any(t in r_url for t in trackers):
                    try:
                        await route.abort()
                    except Exception:
                        pass
                else:
                    try:
                        await route.continue_()
                    except Exception:
                        pass

            try:
                await self._context.route("**/*", _filter_routes)
            except Exception:
                pass

        page = await self._context.new_page()
        page.set_default_timeout(settings.BROWSER_TIMEOUT_MS)

        return self._context, page

    async def save_session_state(self) -> None:
        """Persist cookies and local storage state to disk."""
        if self._context:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            await self._context.storage_state(path=str(self.state_file))
            logger.info(f"Persisted authentication state to: {self.state_file}")

    async def close(self) -> None:
        """Safely clean up contexts and browser instances."""
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.debug(f"Closed browser session for [{self.platform_name}]")
