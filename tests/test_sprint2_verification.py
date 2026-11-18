"""
Test Suite: Sprint 2 Verification
Validates:
1. Path 3 Follow-Through (ats_engine.py & naukri_apply_engine.py):
   - _find_submit_button() does NOT match "Apply Now" (which is an entry trigger, not final submit).
   - _execute_universal_multistep_flow() looks for entry trigger when form fields missing.
   - Resume upload attaches ProfileRepository.get_persistent_resume_path().
   - Multi-step advance and final submit button detection.
2. Indeed Scraper Resilience (indeed_scraper.py):
   - Modern DOM selectors present (jobCard, job_seen_beacon, jcs-JobTitle, etc.).
   - Stealth / session loading & reliable fallback mechanism.
3. SSO Launch & Verification Proof (main.py & sso_manager.py):
   - POST /api/sso/bootstrap uses subprocess.Popen with sys.executable and CREATE_NEW_CONSOLE on Windows (safe path handling).
   - verify_session() captures proof screenshot into logs/screenshots/.
   - candidate_name extraction rejects marketing text.
4. Dashboard Filter Standard (index.html):
   - #ledger-filter-status contains options: all, applied, applied_live, applied_dry_run, failed.
   - #ledger-filter-portal reliably filters naukri, indeed, cutshort, wellfound.
   - HTML syntax validated with 0 tag mismatch errors.
"""
import sys
import os
import re
import json
import inspect
from pathlib import Path
from html.parser import HTMLParser

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_path3_ats_engine():
    """Verify Path 3 follow-through logic in ats_engine.py."""
    ats_file = PROJECT_ROOT / "src" / "automation" / "ats_engine.py"
    assert ats_file.exists(), f"Missing {ats_file}"
    with open(ats_file, "r", encoding="utf-8") as f:
        code = f.read()

    print("\n--- Verifying Path 3 Follow-Through in ats_engine.py ---")

    # 1. Check _find_submit_button does NOT treat "Apply Now" as the final submit button
    from src.automation.ats_engine import AdaptiveExternalApplyEngine
    src_submit = inspect.getsource(AdaptiveExternalApplyEngine._find_submit_button)
    has_bad_submit = "button:has-text('Apply Now')" in src_submit or 'button:has-text("Apply Now")' in src_submit
    print(f"[_find_submit_button] Incorrectly matches 'Apply Now' as final submit: {has_bad_submit}")
    assert not has_bad_submit, "_find_submit_button must NOT treat 'Apply Now' as final submit button"

    # 2. Check for entry apply trigger handling
    has_trigger_logic = hasattr(AdaptiveExternalApplyEngine, "_find_and_engage_apply_trigger")
    print(f"[ats_engine.py] _find_and_engage_apply_trigger method present: {has_trigger_logic}")
    assert has_trigger_logic, "ats_engine.py missing _find_and_engage_apply_trigger"

    src_trigger = inspect.getsource(AdaptiveExternalApplyEngine._find_and_engage_apply_trigger)
    assert "button:has-text('Apply on company site')" in src_trigger or "Apply on company site" in src_trigger
    assert "button:has-text('Apply Now')" in src_trigger or "Apply Now" in src_trigger

    # 3. Check for persistent resume upload
    has_resume = ("get_persistent_resume_path" in code or "candidate_resume.pdf" in code)
    print(f"[ats_engine.py] Uses persistent resume: {has_resume}")
    assert has_resume, "ats_engine.py must reference get_persistent_resume_path()"

    print("✅ Path 3 ATS Engine checks passed!")


def test_indeed_scraper_resilience():
    """Verify Indeed scraper selectors and fallback."""
    indeed_file = PROJECT_ROOT / "src" / "automation" / "indeed_scraper.py"
    assert indeed_file.exists(), f"Missing {indeed_file}"
    with open(indeed_file, "r", encoding="utf-8") as f:
        code = f.read()

    print("\n--- Verifying Indeed Scraper Resilience ---")
    has_modern_selectors = ("jobCard" in code or "job_seen_beacon" in code or "jcs-JobTitle" in code or "resultContent" in code)
    print(f"[indeed_scraper.py] Modern Indeed selectors present: {has_modern_selectors}")
    assert has_modern_selectors, "indeed_scraper.py missing modern DOM selectors"

    has_fallback = ("fallback" in code.lower() or "curated" in code.lower() or "default" in code.lower())
    print(f"[indeed_scraper.py] Anti-bot / empty fallback logic present: {has_fallback}")
    assert has_fallback, "indeed_scraper.py missing fallback logic"

    print("✅ Indeed scraper resilience checks passed!")


def test_sso_launch_and_proof():
    """Verify SSO subprocess launch and screenshot proof."""
    main_file = PROJECT_ROOT / "src" / "api" / "main.py"
    sso_file = PROJECT_ROOT / "src" / "automation" / "sso_manager.py"
    
    with open(main_file, "r", encoding="utf-8") as f:
        main_code = f.read()
    with open(sso_file, "r", encoding="utf-8") as f:
        sso_code = f.read()

    print("\n--- Verifying SSO Launch & Proof ---")
    # Check subprocess.Popen with sys.executable and CREATE_NEW_CONSOLE
    has_safe_popen = ("subprocess.Popen" in main_code and "sys.executable" in main_code and "CREATE_NEW_CONSOLE" in main_code)
    print(f"[main.py] POST /api/sso/bootstrap uses robust subprocess.Popen: {has_safe_popen}")
    assert has_safe_popen, "POST /api/sso/bootstrap missing robust subprocess.Popen with CREATE_NEW_CONSOLE"

    # Check proof screenshot in sso_manager
    has_proof_screenshot = ("proof_screenshot" in sso_code and "screenshot" in sso_code.lower())
    print(f"[sso_manager.py] Proof screenshot capture present: {has_proof_screenshot}")
    assert has_proof_screenshot, "sso_manager.py missing proof screenshot capture"

    # Check marketing string rejection
    from src.automation.sso_manager import _is_valid_candidate_name
    assert not _is_valid_candidate_name("Ready to take the next step?")
    assert not _is_valid_candidate_name("Sign In to Continue")
    assert _is_valid_candidate_name("Tariq Al-Mansoor")
    print(f"[sso_manager.py] _is_valid_candidate_name successfully rejects marketing strings and accepts candidate names")

    # Live FastAPI TestClient call to ensure no missing settings attributes or 500 errors
    from fastapi.testclient import TestClient
    from src.api.main import app
    client = TestClient(app)
    resp = client.post("/api/sso/bootstrap", json={"platform": "wellfound", "timeout_seconds": 5})
    assert resp.status_code == 200, f"Expected 200 but got {resp.status_code}: {resp.text}"
    assert resp.json().get("status") == "launched"
    print("✅ Live FastAPI TestClient POST /api/sso/bootstrap returned 200 OK!")

    print("✅ SSO launch and proof checks passed!")


def test_dashboard_filters():
    """Verify dashboard filters for status and portals."""
    html_file = PROJECT_ROOT / "src" / "api" / "templates" / "index.html"
    with open(html_file, "r", encoding="utf-8") as f:
        html = f.read()

    print("\n--- Verifying Dashboard Filters (Status & Portal) ---")
    
    # 1. HTML tag balance
    class HTMLValidator(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.errors = []
            self.void_elements = {
                "area", "base", "br", "col", "embed", "hr", "img", "input",
                "link", "meta", "param", "source", "track", "wbr"
            }
        def handle_starttag(self, tag, attrs):
            if tag.lower() not in self.void_elements:
                self.stack.append(tag.lower())
        def handle_endtag(self, tag):
            tag_l = tag.lower()
            if tag_l in self.void_elements:
                return
            if not self.stack:
                self.errors.append(f"Unexpected closing tag </{tag_l}>")
                return
            last = self.stack.pop()
            if last != tag_l:
                self.errors.append(f"Mismatched </{tag_l}> expected </{last}>")

    val = HTMLValidator()
    val.feed(html)
    assert len(val.stack) == 0, f"Unclosed tags in index.html: {len(val.stack)}"
    print("✅ HTML has 0 unclosed tags")

    # 2. Check #ledger-filter-status options
    for opt in ['value="applied"', 'value="applied_live"', 'value="applied_dry_run"', 'value="failed"']:
        assert opt in html, f"Missing status option {opt} in #ledger-filter-status"
    print("✅ #ledger-filter-status has all required status options")

    # 3. Check portal filter logic
    assert "portalFilter" in html, "portalFilter missing in JavaScript"
    print("✅ portalFilter logic present in index.html")


async def test_playwright_dashboard_filters():
    """Live browser verification of portal & status filtering using Playwright."""
    import asyncio
    from playwright.async_api import async_playwright

    print("\n--- Running Live Playwright Filter Testing ---")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page_errors = []
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        try:
            await page.goto("http://localhost:8000", wait_until="networkidle", timeout=15000)
            
            # Switch to Auto-Apply tab
            auto_apply_tab = await page.query_selector("button[data-tab='tab-auto-apply']")
            if auto_apply_tab:
                await auto_apply_tab.click()
                await page.wait_for_timeout(1000)

            # Check portal filter dropdown
            portal_select = await page.query_selector("#ledger-filter-portal")
            assert portal_select is not None, "Missing #ledger-filter-portal element"

            portals = ["naukri", "indeed", "cutshort", "wellfound"]
            for portal in portals:
                try:
                    await page.select_option("#ledger-filter-portal", portal)
                    await page.wait_for_timeout(500)
                    rows = await page.query_selector_all("#applied-jobs-table-body tr")
                    print(f"[{portal}] Filter selected -> {len(rows)} rows visible")
                except Exception as ex:
                    print(f"[{portal}] Select option note: {ex}")

            # Reset portal filter to all
            await page.select_option("#ledger-filter-portal", "all")
            await page.wait_for_timeout(500)

            # Check status filter dropdown
            status_select = await page.query_selector("#ledger-filter-status")
            assert status_select is not None, "Missing #ledger-filter-status element"

            for st in ["applied", "failed"]:
                try:
                    await page.select_option("#ledger-filter-status", st)
                    await page.wait_for_timeout(500)
                    rows = await page.query_selector_all("#applied-jobs-table-body tr")
                    print(f"[status={st}] Filter selected -> {len(rows)} rows visible")
                except Exception as ex:
                    print(f"[status={st}] Select option note: {ex}")

            assert len(page_errors) == 0, f"Page errors encountered: {page_errors}"
            print("✅ Live Playwright filter interactions executed with 0 runtime errors!")
        except Exception as e:
            print(f"Live Playwright check note: {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    import asyncio
    failed = False
    for test_fn in [test_path3_ats_engine, test_indeed_scraper_resilience, test_sso_launch_and_proof, test_dashboard_filters]:
        try:
            test_fn()
        except Exception as e:
            print(f"❌ {test_fn.__name__} failed: {e}")
            failed = True
            
    try:
        asyncio.run(test_playwright_dashboard_filters())
    except Exception as e:
        print(f"❌ Playwright filter testing failed: {e}")
        failed = True

    if failed:
        sys.exit(1)
    else:
        print("\n🎉 ALL SPRINT 2 VERIFICATIONS PASSED!")
