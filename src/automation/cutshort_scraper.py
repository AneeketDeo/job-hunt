"""
Cutshort Job Scraper & Auto-Apply Engine
Uses Cutshort's API to fetch GenAI/LLM/Agentic job listings and applies via
the Adaptive External ATS engine (Path 3).
"""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from config.settings import settings
from src.automation.browser_factory import safe_playwright_exec
from src.automation.naukri_apply_engine import NaukriAutoApplyEngine
from src.core.profile_repository import ProfileRepository

CUTSHORT_SEARCH_URL = "https://cutshort.io/api/v1/jobs"
CUTSHORT_KEYWORDS = [
    "GenAI", "LLM", "Agentic AI", "RAG", "AI Engineer",
    "Machine Learning", "NLP", "Generative AI"
]
BLACKLISTED_COMPANIES = [
    "tcs", "accenture", "infosys", "wipro", "cognizant",
    "hcl technologies", "tech mahindra"
]


def _make_job_id(title: str, company: str, url: str) -> str:
    raw = f"{title}_{company}_{url}"
    return "cs_" + hashlib.md5(raw.encode()).hexdigest()[:12]


class CutshortJobScraper:
    """Scrapes Cutshort.io via API and applies via Adaptive External ATS."""

    LEDGER_FILE = settings.DATA_DIR / "applied_jobs.json"
    BACKUP_LEDGER_FILE = settings.DATA_DIR / "cutshort_applied_jobs.json"

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.profile_repo = ProfileRepository()

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

    async def scrape_jobs(self, keywords: List[str] = None, max_jobs: int = 30) -> List[Dict[str, Any]]:
        """Fetch GenAI jobs from Cutshort API."""
        if keywords is None:
            keywords = CUTSHORT_KEYWORDS
        
        jobs = []
        seen_ids = set()

        async with httpx.AsyncClient(timeout=15) as client:
            for kw in keywords[:4]:  # Limit API calls
                try:
                    resp = await client.get(
                        CUTSHORT_SEARCH_URL,
                        params={"q": kw, "locationIds": "2", "type": "fulltime"},  # 2 = Pune approx
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    if resp.status_code != 200:
                        logger.warning(f"[Cutshort] API returned {resp.status_code} for '{kw}'")
                        continue
                    data = resp.json()
                    listings = data.get("data", data.get("jobs", []))
                    for item in listings:
                        title = item.get("title", "")
                        company = item.get("company", {}).get("name", "") if isinstance(item.get("company"), dict) else item.get("company", "")
                        url = item.get("url") or item.get("external_url") or f"https://cutshort.io/job/{item.get('id', '')}"
                        location = item.get("location", "Pune, India")
                        
                        if not title or not company or not url:
                            continue
                        
                        # Blacklist check
                        if any(bl in company.lower() for bl in BLACKLISTED_COMPANIES):
                            continue
                        
                        job_id = _make_job_id(title, company, url)
                        if job_id in seen_ids:
                            continue
                        seen_ids.add(job_id)
                        
                        jobs.append({
                            "job_id": job_id,
                            "title": title,
                            "company": company,
                            "url": url,
                            "location": location,
                            "age_days": 0,
                            "date_posted_str": "Recent",
                            "source": "cutshort",
                        })
                        if len(jobs) >= max_jobs:
                            break
                except Exception as e:
                    logger.warning(f"[Cutshort] Error fetching '{kw}': {e}")
                
                if len(jobs) >= max_jobs:
                    break

        logger.info(f"[Cutshort] Scraped {len(jobs)} unique jobs across keywords")
        return jobs

    async def run_autonomous_apply_cycle(
        self,
        max_jobs_to_apply: int = 5,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """Scrape Cutshort and apply via Path 3 Adaptive Engine."""
        ledger = self.load_ledger()
        scraped = await self.scrape_jobs()
        
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
        
        logger.info(f"[Cutshort] Scraped={len(scraped)}, Unapplied={len(unapplied)}, Queue={len(apply_queue)}")
        
        apply_engine = NaukriAutoApplyEngine(headless=self.headless, dry_run=dry_run)
        applied_results = []
        applied_this_run: set = set()

        for idx, job in enumerate(apply_queue):
            if job["job_id"] in applied_this_run:
                continue
            applied_this_run.add(job["job_id"])
            prev_retry_count = ledger.get(job["job_id"], {}).get("retry_count", job.get("retry_count", 0))
            
            logger.info(f"[Cutshort {idx+1}/{len(apply_queue)}] Applying: {job['title']} @ {job['company']}")
            try:
                result = await apply_engine.apply_to_job(
                    job_url=job["url"],
                    location="Pune, India",
                    dry_run=dry_run,
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
                logger.error(f"[Cutshort] Error on {job['title']}: {e}")
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
            "source": "cutshort",
            "jobs_scraped": len(scraped),
            "already_applied_skipped": len(scraped) - len(unapplied),
            "jobs_targeted": len(apply_queue),
            "applications_executed": applied_results,
            "dry_run": dry_run,
        }
