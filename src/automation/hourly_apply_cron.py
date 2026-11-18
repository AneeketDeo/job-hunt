import asyncio
import email
import json
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, Optional

from config.settings import settings
from src.core.logger import logger
from src.automation.browser_factory import safe_playwright_exec
from src.automation.naukri_scraper import NaukriJobScraper


async def _execute_scraper_cycle(dry_run: bool, portals: list = None) -> Dict[str, Any]:
    """Execute autonomous apply cycle within dedicated Proactor event loop on Windows.
    Supports multi-portal aggregation: naukri (default), cutshort, indeed, wellfound."""
    if portals is None:
        portals = ["naukri"]
    
    all_results = {}
    total_scraped = 0
    total_applied = 0
    all_executions = []

    # Naukri (always primary)
    if "naukri" in portals:
        scraper = NaukriJobScraper(headless=True)
        result = await scraper.run_autonomous_apply_cycle(
            max_jobs_to_apply=5,
            max_age_days=20,
            dry_run=dry_run,
            target_location="Pune, India",
        )
        all_results["naukri"] = result
        total_scraped += result.get("jobs_scraped", 0)
        total_applied += result.get("jobs_targeted", 0)
        all_executions.extend(result.get("applications_executed", []))

    # Cutshort
    if "cutshort" in portals:
        try:
            from src.automation.cutshort_scraper import CutshortJobScraper
            cs_scraper = CutshortJobScraper(headless=True)
            cs_result = await cs_scraper.run_autonomous_apply_cycle(
                max_jobs_to_apply=5, dry_run=dry_run
            )
            all_results["cutshort"] = cs_result
            total_scraped += cs_result.get("jobs_scraped", 0)
            total_applied += cs_result.get("jobs_targeted", 0)
            all_executions.extend(cs_result.get("applications_executed", []))
        except Exception as e:
            logger.warning(f"[CRON] Cutshort cycle failed: {e}")
            all_results["cutshort"] = {"status": "error", "message": str(e)}

    # Indeed
    if "indeed" in portals:
        try:
            from src.automation.indeed_scraper import IndeedJobScraper
            indeed_scraper = IndeedJobScraper(headless=True)
            indeed_result = await indeed_scraper.run_autonomous_apply_cycle(
                max_jobs_to_apply=5, dry_run=dry_run
            )
            all_results["indeed"] = indeed_result
            total_scraped += indeed_result.get("jobs_scraped", 0)
            total_applied += indeed_result.get("jobs_targeted", 0)
            all_executions.extend(indeed_result.get("applications_executed", []))
        except Exception as e:
            logger.warning(f"[CRON] Indeed cycle failed: {e}")
            all_results["indeed"] = {"status": "error", "message": str(e)}

    # Wellfound
    if "wellfound" in portals:
        try:
            from src.automation.wellfound_scraper import WellfoundJobScraper
            wf_scraper = WellfoundJobScraper(headless=True)
            wf_result = await wf_scraper.run_autonomous_apply_cycle(
                max_jobs_to_apply=5, dry_run=dry_run
            )
            all_results["wellfound"] = wf_result
            total_scraped += wf_result.get("jobs_scraped", 0)
            total_applied += wf_result.get("jobs_targeted", 0)
            all_executions.extend(wf_result.get("applications_executed", []))
        except Exception as e:
            logger.warning(f"[CRON] Wellfound cycle failed: {e}")
            all_results["wellfound"] = {"status": "error", "message": str(e)}

    return {
        "status": "cycle_completed",
        "portals_run": portals,
        "jobs_scraped": total_scraped,
        "jobs_targeted": total_applied,
        "applications_executed": all_executions,
        "portal_results": all_results,
        "dry_run": dry_run,
    }


class HourlyApplyCron:
    """Hourly background autonomous scraper and auto-apply cron job.

    Executes every 1 hour, discovers fresh GenAI listings (<= 20d),
    applies through Path 1, 2, or 3, and dispatches a comprehensive
    application digest to the candidate's email.
    """

    CRON_FILE = settings.DATA_DIR / "cron_history.json"
    DEFAULT_INTERVAL_SECONDS = 3600  # 1 hour

    def __init__(self, recipient_email: str = "aneeketdeowork@gmail.com"):
        self.recipient_email = recipient_email
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self.last_run: Optional[str] = None
        self.next_run: Optional[str] = None
        self.total_cycles: int = 0
        self._load_history()

    def _load_history(self) -> None:
        if self.CRON_FILE.exists():
            try:
                with open(self.CRON_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.last_run = data.get("last_run")
                    self.total_cycles = data.get("total_cycles", 0)
            except Exception:
                pass

    def _save_history(self, cycle_summary: Dict[str, Any]) -> None:
        self.CRON_FILE.parent.mkdir(parents=True, exist_ok=True)
        history = {
            "last_run": self.last_run,
            "next_run": self.next_run,
            "total_cycles": self.total_cycles,
            "latest_summary": cycle_summary,
            "updated_at": datetime.now().isoformat(),
        }
        with open(self.CRON_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)

    def start(self) -> Dict[str, Any]:
        """Start the background 1-hour cron loop."""
        if self.is_running and self._task and not self._task.done():
            return {"status": "already_running", "message": "Hourly cron is already active."}

        self.is_running = True
        self._task = asyncio.create_task(self._cron_loop())
        logger.info(f"Hourly Autonomous Apply Cron started. Recipient: {self.recipient_email}")
        return {"status": "started", "interval_seconds": self.DEFAULT_INTERVAL_SECONDS, "recipient": self.recipient_email}

    def stop(self) -> Dict[str, Any]:
        """Stop the background cron loop."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("Hourly Autonomous Apply Cron stopped.")
        return {"status": "stopped", "message": "Hourly cron stopped successfully."}

    def get_status(self) -> Dict[str, Any]:
        """Return current status of the hourly cron job."""
        return {
            "is_running": self.is_running and bool(self._task and not self._task.done()),
            "last_run": self.last_run,
            "next_run": self.next_run,
            "total_cycles": self.total_cycles,
            "recipient_email": self.recipient_email,
            "frequency": "Every 1 Hour (3600s)",
        }

    async def _cron_loop(self) -> None:
        """Loop running every hour."""
        while self.is_running:
            try:
                now = datetime.now()
                self.last_run = now.isoformat()
                self.next_run = (now + timedelta(seconds=self.DEFAULT_INTERVAL_SECONDS)).isoformat()
                self.total_cycles += 1

                logger.info(f"[CRON] Running hourly autonomous apply cycle #{self.total_cycles} across multi-portal feeds...")
                summary = await self.run_single_cycle(dry_run=False, portals=["naukri", "indeed", "cutshort", "wellfound"])
                self._save_history(summary)

                # Send email report
                await self._send_email_report(summary)

            except asyncio.CancelledError:
                logger.info("[CRON] Hourly cron loop cancelled.")
                break
            except Exception as e:
                logger.error(f"[CRON] Error during hourly cycle: {e}")

            # Sleep 1 hour
            await asyncio.sleep(self.DEFAULT_INTERVAL_SECONDS)

    async def run_single_cycle(self, dry_run: bool = False, portals: Optional[list] = None) -> Dict[str, Any]:
        """Execute one autonomous apply cycle across multi-portal feeds safely with Proactor loop."""
        if portals is None:
            portals = ["naukri"]
        return await safe_playwright_exec(_execute_scraper_cycle, dry_run, portals)

    async def _send_email_report(self, cycle_data: Dict[str, Any]) -> bool:
        """Format and dispatch the email summary report to the candidate."""
        subject = f"🚀 [Job Hunt Report] Hourly Autonomous Apply Digest ({datetime.now().strftime('%d %b %H:%M')})"
        
        jobs_targeted = cycle_data.get("jobs_targeted", 0)
        jobs_scraped = cycle_data.get("jobs_scraped", 0)
        skipped = cycle_data.get("already_applied_skipped", 0)
        excluded_skipped = cycle_data.get("excluded_companies_skipped", 0)
        executed = cycle_data.get("applications_executed", [])

        # Build clean HTML email body
        items_html = ""
        for item in executed:
            j = item.get("job", {})
            r = item.get("result", {})
            status_color = "#10b981" if "success" in r.get("status", "") or "verified" in r.get("status", "") else "#f59e0b"
            items_html += f"""
            <tr style="border-bottom: 1px solid #334155;">
                <td style="padding: 10px; font-weight: bold; color: #f8fafc;">{j.get('title', 'Role')}</td>
                <td style="padding: 10px; color: #38bdf8;">{j.get('company', 'Company')}</td>
                <td style="padding: 10px; color: #94a3b8;">{j.get('date_posted_str', 'Fresh')}</td>
                <td style="padding: 10px; color: #c084fc;">{r.get('path', 'Applied')}</td>
                <td style="padding: 10px; font-weight: bold; color: {status_color};">{r.get('status', 'Completed')}</td>
            </tr>
            """

        if not items_html:
            items_html = "<tr><td colspan='5' style='padding: 12px; color: #94a3b8; text-align: center;'>No new unapplied listings found during this cycle. All fresh jobs already processed!</td></tr>"

        html_content = f"""
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0f172a; color: #e2e8f0; padding: 20px;">
            <div style="max-width: 650px; margin: 0 auto; background-color: #1e293b; border-radius: 12px; border: 1px solid #334155; overflow: hidden;">
                <div style="background: linear-gradient(135deg, #059669, #0284c7); padding: 20px; color: #ffffff;">
                    <h2 style="margin: 0; font-size: 20px;">Autonomous Job Hunt Digest</h2>
                    <p style="margin: 5px 0 0; opacity: 0.9; font-size: 13px;">Hourly Autopilot Run • Pune & Dubai GenAI Targets</p>
                </div>
                <div style="padding: 20px;">
                    <div style="display: flex; gap: 10px; margin-bottom: 20px;">
                        <div style="background-color: #0f172a; padding: 12px; border-radius: 8px; flex: 1; text-align: center; border: 1px solid #334155;">
                            <span style="display: block; font-size: 11px; color: #94a3b8;">Fresh Jobs (&le;20d)</span>
                            <strong style="font-size: 18px; color: #f8fafc;">{jobs_scraped}</strong>
                        </div>
                        <div style="background-color: #0f172a; padding: 12px; border-radius: 8px; flex: 1; text-align: center; border: 1px solid #334155;">
                            <span style="display: block; font-size: 11px; color: #94a3b8;">Applications Processed</span>
                            <strong style="font-size: 18px; color: #10b981;">{jobs_targeted}</strong>
                        </div>
                        <div style="background-color: #0f172a; padding: 12px; border-radius: 8px; flex: 1; text-align: center; border: 1px solid #334155;">
                            <span style="display: block; font-size: 11px; color: #94a3b8;">Already In Ledger</span>
                            <strong style="font-size: 18px; color: #f59e0b;">{skipped}</strong>
                        </div>
                    </div>

                    <h3 style="font-size: 14px; margin-bottom: 10px; color: #e2e8f0;">Applications Executed in this Cycle:</h3>
                    <table style="width: 100%; border-collapse: collapse; font-size: 12px; text-align: left;">
                        <thead>
                            <tr style="background-color: #0f172a; color: #94a3b8; font-size: 11px; text-transform: uppercase;">
                                <th style="padding: 10px;">Role</th>
                                <th style="padding: 10px;">Company</th>
                                <th style="padding: 10px;">Age</th>
                                <th style="padding: 10px;">Path</th>
                                <th style="padding: 10px;">Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items_html}
                        </tbody>
                    </table>
                    
                    <p style="margin-top: 20px; font-size: 11px; color: #64748b; text-align: center;">
                        Generated autonomously by Antigravity Job Hunt Orchestrator for {self.recipient_email}
                    </p>
                </div>
            </div>
        </body>
        </html>
        """

        # 1. Dispatch via SMTP if credentials exist in .env
        smtp_user = settings.GMAIL_USER_EMAIL or self.recipient_email
        smtp_pass = getattr(settings, "GMAIL_APP_PASSWORD", "") or os.environ.get("GMAIL_APP_PASSWORD", "")

        sent_via_smtp = False
        if smtp_user and smtp_pass:
            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = smtp_user
                msg["To"] = self.recipient_email
                msg.attach(MIMEText(html_content, "html"))

                # Try SSL 465 first, fallback to TLS 587
                try:
                    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=12) as server:
                        server.login(smtp_user, smtp_pass)
                        server.sendmail(smtp_user, self.recipient_email, msg.as_string())
                    sent_via_smtp = True
                    logger.info(f"[CRON] Successfully dispatched email report to {self.recipient_email} via Gmail SSL (port 465).")
                except Exception as ssl_err:
                    logger.debug(f"[CRON] SSL 465 attempt failed: {ssl_err}. Trying TLS 587...")
                    with smtplib.SMTP("smtp.gmail.com", 587, timeout=12) as server:
                        server.starttls()
                        server.login(smtp_user, smtp_pass)
                        server.sendmail(smtp_user, self.recipient_email, msg.as_string())
                    sent_via_smtp = True
                    logger.info(f"[CRON] Successfully dispatched email report to {self.recipient_email} via Gmail TLS (port 587).")
            except Exception as e:
                logger.warning(f"[CRON] Gmail SMTP send error: {e}. If using 2FA, generate a Google App Password at https://myaccount.google.com/apppasswords")

        # 2. Dispatch to webhook if configured (Discord, Slack, or generic webhook)
        webhook_url = settings.FALLBACK_WEBHOOK_URL or os.environ.get("FALLBACK_WEBHOOK_URL", "")
        if webhook_url:
            try:
                import urllib.request
                from datetime import timezone
                is_discord = "discord.com/api/webhooks" in webhook_url or "discordapp.com/api/webhooks" in webhook_url

                if is_discord:
                    executed_lines = []
                    for idx, item in enumerate(executed[:6], 1):
                        j = item.get("job", {})
                        r = item.get("result", {})
                        p = r.get("path", "unknown").replace("_", " ").title()
                        s = r.get("status", "completed").replace("_", " ").title()
                        executed_lines.append(f"**{idx}. {j.get('company', 'Unknown')}** - {j.get('title', 'Role')}\n└ *{p}* • `{s}`")

                    app_desc = "\n".join(executed_lines) if executed_lines else "No new applications executed in this cycle."

                    discord_payload = {
                        "username": "Job Hunt Autopilot",
                        "avatar_url": "https://img.icons8.com/color/96/artificial-intelligence.png",
                        "embeds": [
                            {
                                "title": f"🚀 Autonomous Apply Digest ({datetime.now().strftime('%d %b %H:%M')})",
                                "color": 0x10b981 if jobs_targeted > 0 else 0x06b6d4,
                                "description": f"**Applications Processed:** `{jobs_targeted}` | **Fresh Scanned:** `{jobs_scraped}`\n**Skipped (Ledger):** `{skipped}` | **Blacklisted:** `{excluded_skipped}`\n\n### Application Results:\n{app_desc}",
                                "fields": [
                                    {"name": "🎯 Target Locations", "value": "Pune (18-21+ LPA) • Dubai (AED 18K-22K)", "inline": True},
                                    {"name": "⚙️ Execution", "value": "Windows Proactor Headless", "inline": True},
                                    {"name": "👤 Candidate", "value": "Aneeket Deo", "inline": True}
                                ],
                                "footer": {"text": "Antigravity Autonomous Job Hunt Orchestrator"},
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            }
                        ]
                    }
                    hook_data = json.dumps(discord_payload).encode("utf-8")
                else:
                    generic_payload = {
                        "text": f"🚀 Autonomous Job Hunt Digest: {jobs_targeted} applications processed, {jobs_scraped} fresh jobs scanned.",
                        "content": f"🚀 Autonomous Job Hunt Digest: {jobs_targeted} applications processed, {jobs_scraped} fresh jobs scanned.",
                        "subject": subject,
                        "cycle_data": {
                            "jobs_targeted": jobs_targeted,
                            "jobs_scraped": jobs_scraped,
                            "skipped": skipped,
                            "excluded_skipped": excluded_skipped
                        }
                    }
                    hook_data = json.dumps(generic_payload).encode("utf-8")

                req = urllib.request.Request(
                    webhook_url,
                    data=hook_data,
                    headers={"Content-Type": "application/json", "User-Agent": "JobHuntOrchestrator"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    logger.info(f"[CRON] Successfully dispatched digest summary to webhook ({webhook_url[:35]}...). HTTP {resp.status}")
            except Exception as hook_err:
                logger.warning(f"[CRON] Webhook dispatch notice: {hook_err}")

        # 3. Always save structured HTML report in dedicated reports directory
        settings.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        digest_file = settings.REPORTS_DIR / f"email_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        with open(digest_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"[CRON] Hourly email digest generated and saved to: {digest_file} (Recipient: {self.recipient_email})")
        return sent_via_smtp or True


# Global singleton instance
hourly_cron_service = HourlyApplyCron()
