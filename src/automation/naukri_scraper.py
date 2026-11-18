import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.async_api import Page

from config.settings import settings
from src.core.logger import logger
from src.core.profile_repository import ProfileRepository
from src.automation.browser_factory import BrowserFactory
from src.automation.naukri_apply_engine import NaukriAutoApplyEngine


class NaukriJobScraper:
    """Autonomous Naukri Scraper and Auto-Apply Pipeline

    Scrapes live Naukri job listings within a 20-day timeframe for target
    GenAI/Agentic engineering roles across Pune and Dubai, deduplicates
    against a local ledger, and auto-applies autonomously.
    """

    APPLIED_LEDGER_PATH = settings.DATA_DIR / "applied_jobs.json"

    DEFAULT_QUERIES = [
        "https://www.naukri.com/genai-developer-jobs-in-pune",
        "https://www.naukri.com/llm-engineer-jobs-in-pune",
        "https://www.naukri.com/agentic-ai-developer-jobs-in-pune",
        "https://www.naukri.com/genai-jobs-in-dubai",
    ]

    def __init__(self, headless: Optional[bool] = None):
        self.headless = settings.HEADLESS if headless is None else headless
        self._ensure_ledger_file()

    def _ensure_ledger_file(self) -> None:
        """Initialize applied_jobs.json ledger if it doesn't exist."""
        if not self.APPLIED_LEDGER_PATH.exists():
            self.APPLIED_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self.APPLIED_LEDGER_PATH, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=2)

    def load_applied_ledger(self) -> Dict[str, Dict[str, Any]]:
        """Load history of applied jobs."""
        try:
            with open(self.APPLIED_LEDGER_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error loading applied ledger: {e}")
            return {}

    def save_applied_ledger(self, ledger: Dict[str, Dict[str, Any]]) -> None:
        """Persist applied jobs history to disk."""
        with open(self.APPLIED_LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2)

    @staticmethod
    def parse_job_age_days(date_str: str) -> int:
        """Convert Naukri relative posting date string to integer days."""
        s = date_str.lower().strip()
        if not s or any(w in s for w in ["just now", "today", "few hours", "hour"]):
            return 0
        if "week" in s:
            nums = re.findall(r"\d+", s)
            weeks = int(nums[0]) if nums else 1
            return weeks * 7
        if "month" in s:
            return 30
        nums = re.findall(r"\d+", s)
        if nums:
            return int(nums[0])
        return 0

    @staticmethod
    def extract_job_id(url: str) -> str:
        """Extract unique numerical identifier from Naukri URL."""
        nums = re.findall(r"\d{10,}", url)
        if nums:
            return nums[-1]
        # Fallback to slug hash
        return str(abs(hash(url)))

    async def scrape_recent_jobs(
        self,
        search_urls: Optional[List[str]] = None,
        max_age_days: int = 20,
        max_results: int = 40,
        excluded_companies: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Scrape Naukri job cards and filter strictly for age <= max_age_days (20d) and excluded companies."""
        urls_to_scrape = search_urls or self.DEFAULT_QUERIES
        logger.info(
            f"Initiating Naukri scraper across {len(urls_to_scrape)} query feeds "
            f"(Max Age: {max_age_days}d, Excluded Companies: {len(excluded_companies) if excluded_companies else 0})..."
        )

        factory = BrowserFactory(platform_name="naukri", headless=self.headless)
        context, page = await factory.init_session()

        scraped_jobs: List[Dict[str, Any]] = []
        seen_job_ids = set()

        try:
            for feed_url in urls_to_scrape:
                logger.info(f"Scraping Naukri query: {feed_url}")
                try:
                    await page.goto(feed_url, wait_until="domcontentloaded", timeout=35000)
                    await asyncio.sleep(3.5)

                    cards = await page.query_selector_all(
                        ".srp-jobtuple-wrapper, .cust-job-tuple, .jobTuple, article.jobTuple, article"
                    )
                    logger.info(f"Feed [{feed_url}] returned {len(cards)} job card tuples.")

                    for card in cards:
                        if len(scraped_jobs) >= max_results:
                            break

                        title_el = await card.query_selector("a.title, .title")
                        if not title_el:
                            continue

                        title = (await title_el.inner_text()).strip()
                        href = await title_el.get_attribute("href")
                        if not href:
                            continue

                        job_id = self.extract_job_id(href)
                        if job_id in seen_job_ids:
                            continue
                        seen_job_ids.add(job_id)

                        comp_el = await card.query_selector("a.comp-name, .comp-name, .companyInfo a")
                        company = (await comp_el.inner_text()).strip() if comp_el else "Enterprise AI"

                        # Company Exclusion Blacklist Check
                        if excluded_companies and ProfileRepository.is_company_excluded(company, excluded_companies):
                            logger.info(f"[EXCLUDED] Skipping job [{title}] @ [{company}]: Company is in exclusion blacklist.")
                            continue

                        date_el = await card.query_selector(".job-post-day, .date, [class*='date']")
                        date_text = (await date_el.inner_text()).strip() if date_el else "Today"

                        loc_el = await card.query_selector(".loc-wrap span, .location, .locWdth")
                        loc_text = (await loc_el.inner_text()).strip() if loc_el else "Pune / Dubai"

                        exp_el = await card.query_selector(".exp-wrap span, .experience")
                        exp_text = (await exp_el.inner_text()).strip() if exp_el else "3+ YOE"

                        age_days = self.parse_job_age_days(date_text)

                        # Enforce <= 20 days filter requirement
                        if age_days > max_age_days:
                            logger.debug(f"Skipping job [{title}]: age {age_days}d exceeds {max_age_days}d cutoff.")
                            continue

                        scraped_jobs.append({
                            "job_id": job_id,
                            "title": title,
                            "company": company,
                            "url": href,
                            "location": loc_text,
                            "experience": exp_text,
                            "date_posted_str": date_text,
                            "age_days": age_days,
                            "scraped_at": datetime.now().isoformat(),
                        })

                except Exception as e:
                    logger.warning(f"Error scraping query {feed_url}: {e}")
                    continue

                if len(scraped_jobs) >= max_results:
                    break

            logger.info(f"Naukri Scraper finished. Located {len(scraped_jobs)} qualified jobs posted within {max_age_days} days.")
            return scraped_jobs

        finally:
            await factory.close()

    async def run_autonomous_apply_cycle(
        self,
        max_jobs_to_apply: int = 5,
        max_age_days: int = 20,
        dry_run: bool = True,
        target_location: str = "Pune, India",
        excluded_companies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Complete Autonomous Cycle: Scrape fresh listings (<= 20d), filter excluded companies,

        deduplicate against ledger, and apply through Path 1, Path 2, or Path 3 (Workday).
        """
        # Resolve company exclusion list
        repo = ProfileRepository()
        if excluded_companies is None:
            excluded_companies = repo.get_excluded_companies()

        logger.info(
            f"=== Starting Autonomous Naukri Auto-Apply Cycle (Limit: {max_jobs_to_apply}, "
            f"MaxAge: {max_age_days}d, DryRun: {dry_run}, Excluded Companies: {excluded_companies}) ==="
        )

        ledger = self.load_applied_ledger()
        scraped_jobs = await self.scrape_recent_jobs(
            max_age_days=max_age_days,
            excluded_companies=excluded_companies,
        )

        # Deduplicate against ledger and company exclusion list
        unapplied_jobs = []
        excluded_skipped_count = 0
        for j in scraped_jobs:
            job_id = j["job_id"]
            if job_id in ledger:
                entry = ledger[job_id]
                entry_status = entry.get("status", "")
                if entry_status in ("applied", "submitted"):
                    continue
                if entry_status in ("failed", "error"):
                    retry_count = entry.get("retry_count", 0)
                    if retry_count >= 3:
                        continue
                    j["retry_count"] = retry_count
                else:
                    continue

            if excluded_companies and ProfileRepository.is_company_excluded(j["company"], excluded_companies):
                excluded_skipped_count += 1
                logger.info(f"[EXCLUDED] Skipping unapplied job '{j['title']}' @ '{j['company']}'")
                continue
            unapplied_jobs.append(j)

        already_applied_count = len(scraped_jobs) - len(unapplied_jobs) - excluded_skipped_count

        logger.info(
            f"Discovery Stats: Scraped {len(scraped_jobs)} fresh jobs (<= {max_age_days}d). "
            f"Already applied: {already_applied_count}. Excluded companies skipped: {excluded_skipped_count}. "
            f"Unapplied Queue: {len(unapplied_jobs)}."
        )

        apply_queue = unapplied_jobs[:max_jobs_to_apply]
        apply_engine = NaukriAutoApplyEngine(headless=self.headless, dry_run=dry_run)

        applied_results: List[Dict[str, Any]] = []

        applied_this_run: set = set()
        for idx, job in enumerate(apply_queue):
            if excluded_companies and ProfileRepository.is_company_excluded(job["company"], excluded_companies):
                logger.info(f"[EXCLUDED] Pre-apply check: Skipping {job['title']} @ {job['company']}")
                continue
            # Skip if already attempted in this cycle (error guard)
            if job["job_id"] in applied_this_run:
                logger.info(f"[SKIP] Already attempted this cycle: {job['title']} @ {job['company']}")
                continue
            applied_this_run.add(job["job_id"])

            prev_retry_count = ledger.get(job["job_id"], {}).get("retry_count", job.get("retry_count", 0))
            logger.info(f"[{idx + 1}/{len(apply_queue)}] Auto-applying to: {job['title']} @ {job['company']}")
            try:
                # Apply using 3-Path Engine
                app_res = await apply_engine.apply_to_job(
                    job_url=job["url"],
                    location=target_location,
                    dry_run=dry_run,
                    excluded_companies=excluded_companies,
                )

                res_status = app_res.get("status", "applied")
                if res_status in ["failed", "error"]:
                    status = "failed"
                    new_retry_count = prev_retry_count + 1
                    err_msg = app_res.get("message") or "Application failed"
                else:
                    status = "applied"
                    new_retry_count = prev_retry_count
                    err_msg = None

                # Record in ledger
                ledger[job["job_id"]] = {
                    "job_id": job["job_id"],
                    "title": job["title"],
                    "company": job["company"],
                    "url": job["url"],
                    "location": job["location"],
                    "age_days": job.get("age_days", 0),
                    "date_posted_str": job.get("date_posted_str", ""),
                    "applied_at": datetime.now().isoformat(),
                    "path": app_res.get("path", "unknown"),
                    "status": status,
                    "retry_count": new_retry_count,
                    "error_message": err_msg,
                    "dry_run": dry_run,
                    "proof_screenshot": app_res.get("proof_screenshot"),
                    "message": app_res.get("message"),
                }

                self.save_applied_ledger(ledger)

                applied_results.append({
                    "job": job,
                    "result": app_res,
                })

                # Polite human-like delay between job applications
                if idx < len(apply_queue) - 1:
                    await asyncio.sleep(4)

            except Exception as e:
                logger.error(f"Error auto-applying to {job['title']}: {e}")
                new_retry_count = prev_retry_count + 1
                # Record error in ledger so it won't be retried next cycle
                ledger[job["job_id"]] = {
                    "job_id": job["job_id"],
                    "title": job["title"],
                    "company": job["company"],
                    "url": job["url"],
                    "location": job["location"],
                    "age_days": job.get("age_days", 0),
                    "date_posted_str": job.get("date_posted_str", ""),
                    "applied_at": datetime.now().isoformat(),
                    "path": "error",
                    "status": "failed",
                    "retry_count": new_retry_count,
                    "error_message": str(e),
                    "dry_run": dry_run,
                    "proof_screenshot": None,
                    "message": str(e),
                }
                self.save_applied_ledger(ledger)
                applied_results.append({
                    "job": job,
                    "result": {"status": "failed", "message": str(e)},
                })

        return {
            "status": "cycle_completed",
            "jobs_scraped": len(scraped_jobs),
            "already_applied_skipped": already_applied_count,
            "excluded_companies_skipped": excluded_skipped_count,
            "excluded_companies": excluded_companies,
            "jobs_targeted": len(apply_queue),
            "applications_executed": applied_results,
            "total_ledger_count": len(ledger),
            "dry_run": dry_run,
        }


# =============================================================================
# CLI RUNNER
# =============================================================================
if __name__ == "__main__":
    import argparse
    from rich.console import Console
    from rich.table import Table

    parser = argparse.ArgumentParser(description="Autonomous Naukri Job Scraper & Auto-Apply Engine (<= 20d)")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of fresh jobs to apply to in this cycle")
    parser.add_argument("--max-age", type=int, default=20, help="Maximum job posting age in days (default: 20)")
    parser.add_argument("--submit", action="store_true", help="Live submit applications (default: safe dry-run)")
    parser.add_argument("--location", type=str, default="Pune, India", choices=["Pune, India", "Dubai, UAE"])
    parser.add_argument("--interactive", action="store_true", help="Run headed Edge browser to observe live")

    args = parser.parse_args()
    console = Console()

    scraper = NaukriJobScraper(headless=False if args.interactive else None)
    summary = asyncio.run(scraper.run_autonomous_apply_cycle(
        max_jobs_to_apply=args.limit,
        max_age_days=args.max_age,
        dry_run=not args.submit,
        target_location=args.location,
    ))

    table = Table(title="🤖 Autonomous Naukri Auto-Apply Summary")
    table.add_column("Company", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Age", style="yellow")
    table.add_column("Path", style="magenta")
    table.add_column("Status", style="green")

    for item in summary.get("applications_executed", []):
        j = item.get("job", {})
        r = item.get("result", {})
        table.add_row(
            j.get("company", "N/A"),
            j.get("title", "N/A"),
            f"{j.get('age_days', 0)}d ({j.get('date_posted_str', '')})",
            r.get("path", "N/A"),
            r.get("status", "N/A"),
        )

    console.print(table)
    console.print(f"[bold green]Ledger Total:[/bold green] {summary.get('total_ledger_count')} jobs tracked.")
