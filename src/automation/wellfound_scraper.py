"""
Wellfound (AngelList) Job Scraper & Auto-Apply Engine
Scrapes wellfound.com for GenAI/LLM startup roles and applies via
the Adaptive External ATS engine (Path 3).
"""
import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List

from loguru import logger

from config.settings import settings
from src.automation.browser_factory import safe_playwright_exec
from src.automation.naukri_apply_engine import NaukriAutoApplyEngine

WELLFOUND_BASE = "https://wellfound.com"
WELLFOUND_JOBS_URL = "https://wellfound.com/jobs"
WELLFOUND_QUERIES = [
    "generative-ai",
    "llm",
    "machine-learning",
]
BLACKLISTED_COMPANIES = [
    "tcs", "accenture", "infosys", "wipro", "cognizant",
    "hcl technologies", "tech mahindra"
]
LOGIN_EMAIL = "aneeketdeowork@gmail.com"
LOGIN_PASSWORD = "Aneeket$123"


def _make_job_id(title: str, company: str, url: str) -> str:
    raw = f"{title}_{company}_{url}"
    return "wf_" + hashlib.md5(raw.encode()).hexdigest()[:12]


async def _scrape_wellfound_jobs(max_jobs: int = 30) -> List[Dict[str, Any]]:
    """Playwright scraper for wellfound.com job listings."""
    from playwright.async_api import async_playwright

    jobs = []
    seen_ids = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ))
        page = await ctx.new_page()

        # Attempt login
        try:
            await page.goto(f"{WELLFOUND_BASE}/login", timeout=20000, wait_until="domcontentloaded")
            await asyncio.sleep(1.5)
            email_field = await page.query_selector("input[type='email'], input[name='email']")
            pw_field = await page.query_selector("input[type='password'], input[name='password']")
            if email_field and pw_field:
                await email_field.fill(LOGIN_EMAIL)
                await pw_field.fill(LOGIN_PASSWORD)
                submit = await page.query_selector("button[type='submit']")
                if submit:
                    await submit.click()
                    await asyncio.sleep(3)
                    logger.info("[Wellfound] Login attempted")
        except Exception as e:
            logger.warning(f"[Wellfound] Login error (continuing as guest): {e}")

        # Scrape job listings
        for query in WELLFOUND_QUERIES[:2]:
            if len(jobs) >= max_jobs:
                break
            try:
                url = f"{WELLFOUND_JOBS_URL}?role={query}&location=india"
                await page.goto(url, timeout=25000, wait_until="domcontentloaded")
                await asyncio.sleep(2)

                cards = await page.query_selector_all(
                    "[data-test='StartupResult'], .styles_component__BCKne, [class*='JobListing']"
                )
                logger.info(f"[Wellfound] Query='{query}' found {len(cards)} cards")

                for card in cards:
                    try:
                        title_el = await card.query_selector("h2, [class*='title'], [class*='role']")
                        company_el = await card.query_selector("[class*='company'], [class*='startup']")
                        link_el = await card.query_selector("a[href*='/jobs/']")
                        location_el = await card.query_selector("[class*='location']")

                        title = (await title_el.inner_text()).strip() if title_el else ""
                        company = (await company_el.inner_text()).strip() if company_el else ""
                        location_text = (await location_el.inner_text()).strip() if location_el else "India (Remote)"
                        href = await link_el.get_attribute("href") if link_el else ""
                        if href and not href.startswith("http"):
                            href = WELLFOUND_BASE + href

                        if not title or not company or not href:
                            continue
                        if any(bl in company.lower() for bl in BLACKLISTED_COMPANIES):
                            continue

                        job_id = _make_job_id(title, company, href)
                        if job_id in seen_ids:
                            continue
                        seen_ids.add(job_id)

                        jobs.append({
                            "job_id": job_id,
                            "title": title,
                            "company": company,
                            "url": href,
                            "location": location_text,
                            "age_days": 0,
                            "date_posted_str": "Recent",
                            "source": "wellfound",
                        })
                        if len(jobs) >= max_jobs:
                            break
                    except Exception as e:
                        logger.debug(f"[Wellfound] Card parse error: {e}")
            except Exception as e:
                logger.warning(f"[Wellfound] Scrape error for '{query}': {e}")
            await asyncio.sleep(3)

        await browser.close()

    return jobs


class WellfoundJobScraper:
    """Scrapes Wellfound (AngelList) and applies via Adaptive External ATS."""

    LEDGER_FILE = settings.DATA_DIR / "applied_jobs.json"
    BACKUP_LEDGER_FILE = settings.DATA_DIR / "wellfound_applied_jobs.json"

    def __init__(self, headless: bool = True):
        self.headless = headless

    def load_ledger(self) -> Dict[str, Any]:
        data = {}
        if self.LEDGER_FILE.exists():
            try:
                with open(self.LEDGER_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        if not data and self.BACKUP_LEDGER_FILE.exists():
            try:
                with open(self.BACKUP_LEDGER_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
        return data

    def save_ledger(self, ledger: Dict[str, Any]) -> None:
        self.LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
        current = {}
        if self.LEDGER_FILE.exists():
            try:
                with open(self.LEDGER_FILE, "r", encoding="utf-8") as f:
                    current = json.load(f)
            except Exception:
                current = {}
        current.update(ledger)
        with open(self.LEDGER_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        try:
            with open(self.BACKUP_LEDGER_FILE, "w", encoding="utf-8") as f:
                json.dump(ledger, f, indent=2)
        except Exception:
            pass

    async def run_autonomous_apply_cycle(
        self,
        max_jobs_to_apply: int = 5,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        ledger = self.load_ledger()
        try:
            scraped = await safe_playwright_exec(_scrape_wellfound_jobs, 30)
        except Exception as e:
            logger.error(f"[Wellfound] Scrape failed: {e}")
            return {"status": "error", "source": "wellfound", "message": str(e), "jobs_scraped": 0, "jobs_targeted": 0, "applications_executed": []}

        unapplied = []
        for j in scraped:
            jid = j["job_id"]
            if jid in ledger:
                entry = ledger[jid]
                s = entry.get("status", "")
                if s in ("applied", "submitted"):
                    continue
                if s in ("failed", "error"):
                    retry_count = entry.get("retry_count", 0)
                    if retry_count >= 3:
                        continue
                    j["retry_count"] = retry_count
                else:
                    continue
            unapplied.append(j)
        apply_queue = unapplied[:max_jobs_to_apply]
        logger.info(f"[Wellfound] Scraped={len(scraped)}, Unapplied={len(unapplied)}, Queue={len(apply_queue)}")

        apply_engine = NaukriAutoApplyEngine(headless=self.headless, dry_run=dry_run)
        applied_results = []
        applied_this_run: set = set()

        for idx, job in enumerate(apply_queue):
            if job["job_id"] in applied_this_run:
                continue
            applied_this_run.add(job["job_id"])
            prev_retry_count = ledger.get(job["job_id"], {}).get("retry_count", job.get("retry_count", 0))
            logger.info(f"[Wellfound {idx+1}/{len(apply_queue)}] Applying: {job['title']} @ {job['company']}")
            try:
                result = await apply_engine.apply_to_job(
                    job_url=job["url"], location="Pune, India", dry_run=dry_run,
                )
                res_status = result.get("status", "applied")
                if res_status in ["failed", "error"]:
                    status = "failed"
                    new_retry = prev_retry_count + 1
                    err_msg = result.get("message") or "Application failed"
                else:
                    status = "applied"
                    new_retry = prev_retry_count
                    err_msg = None

                ledger[job["job_id"]] = {
                    **job,
                    "applied_at": datetime.now().isoformat(),
                    "path": result.get("path", "path_3_external_ats"),
                    "status": status,
                    "retry_count": new_retry,
                    "error_message": err_msg,
                    "dry_run": dry_run,
                    "proof_screenshot": result.get("proof_screenshot"),
                    "message": result.get("message"),
                }
                self.save_ledger(ledger)
                applied_results.append({"job": job, "result": result})
                if idx < len(apply_queue) - 1:
                    await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"[Wellfound] Error on {job['title']}: {e}")
                ledger[job["job_id"]] = {
                    **job,
                    "applied_at": datetime.now().isoformat(),
                    "path": "error",
                    "status": "failed",
                    "retry_count": prev_retry_count + 1,
                    "error_message": str(e),
                    "dry_run": dry_run,
                    "proof_screenshot": None,
                    "message": str(e),
                }
                self.save_ledger(ledger)
                applied_results.append({"job": job, "result": {"status": "failed", "message": str(e)}})

        return {
            "status": "cycle_completed",
            "source": "wellfound",
            "jobs_scraped": len(scraped),
            "already_applied_skipped": len(scraped) - len(unapplied),
            "jobs_targeted": len(apply_queue),
            "applications_executed": applied_results,
            "dry_run": dry_run,
        }
