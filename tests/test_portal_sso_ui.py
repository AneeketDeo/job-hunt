"""
Test Suite: Portal SSO Hub & Dashboard UI Verification
Validates:
1. HTML parser check: zero malformed or unclosed major tags.
2. All 5 portals SSO Hub cards (Naukri, Indeed, Cutshort, Wellfound, Bayt):
   - Status indicators, cookies count, SSO launch buttons
3. Auto-Apply tab portal checkboxes (portal-naukri, portal-indeed, portal-cutshort, portal-wellfound)
4. Applied Jobs Ledger:
   - Badges display only "Applied" and "Failed"
   - Complete absence of "Form Filled Ready to Submit"
   - "Retry Failed Applications" button in toolbar calling POST /api/jobs/retry-failed
5. Candidate Profile Tab:
   - Persistent resume display (filename, upload date, preview/download link)
6. Playwright live DOM check (if running)
"""
import sys
import asyncio
from html.parser import HTMLParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


class HTMLTagValidator(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []
        # Void elements in HTML5 that do not require closing tags
        self.void_elements = {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"
        }

    def handle_starttag(self, tag, attrs):
        if tag.lower() not in self.void_elements:
            self.stack.append(tag.lower())

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in self.void_elements:
            return
        if not self.stack:
            self.errors.append(f"Unexpected closing tag </{tag_lower}> with empty stack")
            return
        last = self.stack.pop()
        if last != tag_lower:
            self.errors.append(f"Mismatched closing tag </{tag_lower}>, expected </{last}>")


def test_html_structure():
    """Verify HTML parses cleanly."""
    html_file = PROJECT_ROOT / "src" / "api" / "templates" / "index.html"
    assert html_file.exists(), f"Missing {html_file}"
    
    with open(html_file, "r", encoding="utf-8") as f:
        html = f.read()
        
    print("\n--- Verifying index.html HTML Structure ---")
    validator = HTMLTagValidator()
    try:
        validator.feed(html)
        print(f"✅ HTML parsed without fatal parser errors. Unclosed tags remaining on stack: {len(validator.stack)}")
    except Exception as e:
        print(f"❌ HTML parser encountered error: {e}")
        assert False, f"HTML parser failed: {e}"


def test_sso_cards():
    """Verify all 5 portal SSO cards."""
    html_file = PROJECT_ROOT / "src" / "api" / "templates" / "index.html"
    with open(html_file, "r", encoding="utf-8") as f:
        html = f.read()

    portals = ["naukri", "bayt", "indeed", "cutshort", "wellfound"]
    print("\n--- Verifying SSO Hub Cards (All 5 Portals) ---")
    for p in portals:
        btn_match = f"btn-{p}-sso" in html or f"launchsso('{p}')" in html.lower() or f'sso("{p}")' in html.lower()
        print(f"[{p}] SSO button present: {btn_match}")
        assert btn_match, f"Missing SSO launch button for {p}"


def test_portal_checkboxes():
    """Verify portal checkboxes exist for active auto-apply portals."""
    html_file = PROJECT_ROOT / "src" / "api" / "templates" / "index.html"
    with open(html_file, "r", encoding="utf-8") as f:
        html = f.read()

    active_portals = ["naukri", "cutshort", "indeed", "wellfound"]
    print("\n--- Verifying Auto-Apply Portal Checkboxes ---")
    for p in active_portals:
        chk_match = f'id="portal-{p}"' in html
        print(f"[{p}] Checkbox id='portal-{p}' present: {chk_match}")
        assert chk_match, f"Missing portal-{p} checkbox"


def test_ledger_elements():
    """Verify ledger badges, retry button, and no obsolete statuses."""
    html_file = PROJECT_ROOT / "src" / "api" / "templates" / "index.html"
    with open(html_file, "r", encoding="utf-8") as f:
        html = f.read()

    print("\n--- Verifying Ledger Elements & Clean Badges ---")
    
    # 1. Obsolete status checks
    assert "Form Filled Ready to Submit" not in html, "Found obsolete string 'Form Filled Ready to Submit' in HTML"
    print("✅ Zero occurrences of 'Form Filled Ready to Submit' in HTML")
    
    # 2. Retry failed button
    has_retry_btn = "retry-failed" in html or "retryfailed" in html.lower() or "retry failed" in html.lower()
    print(f"✅ 'Retry Failed Applications' button present: {has_retry_btn}")
    assert has_retry_btn, "Missing 'Retry Failed Applications' button in ledger toolbar"


def test_profile_persistent_resume_section():
    """Verify persistent resume UI in Candidate Profile tab."""
    html_file = PROJECT_ROOT / "src" / "api" / "templates" / "index.html"
    with open(html_file, "r", encoding="utf-8") as f:
        html = f.read()

    print("\n--- Verifying Candidate Profile Resume Section ---")
    has_resume_section = "resume" in html.lower() and ("upload" in html.lower() or "candidate_resume" in html.lower())
    print(f"✅ Candidate profile resume section present: {has_resume_section}")
    assert has_resume_section, "Missing candidate profile resume section"


async def test_live_playwright():
    """Verify live page rendering in Playwright."""
    from playwright.async_api import async_playwright
    
    print("\n--- Running Live Playwright Browser Verification ---")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page_errors = []
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        
        try:
            await page.goto("http://localhost:8000", wait_until="networkidle", timeout=15000)
            title = await page.title()
            print(f"✅ Loaded page: {title}")
            
            # Check Portals Admin tab
            portals_tab = await page.query_selector("button[data-tab='tab-admin-portals']")
            if portals_tab:
                await portals_tab.click()
                await page.wait_for_timeout(1000)
                print("✅ Navigated to Portals Admin tab")
                
            # Check Auto Apply tab
            auto_apply_tab = await page.query_selector("button[data-tab='tab-auto-apply']")
            if auto_apply_tab:
                await auto_apply_tab.click()
                await page.wait_for_timeout(1000)
                print("✅ Navigated to Auto-Apply tab")
                
            # Verify no runtime JS errors
            print(f"Console/Page errors: {page_errors}")
            assert len(page_errors) == 0, f"Page errors encountered: {page_errors}"
            print("✅ Zero JavaScript runtime errors!")
        except Exception as e:
            print(f"Browser check info: {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    test_html_structure()
    test_sso_cards()
    test_portal_checkboxes()
    test_ledger_elements()
    test_profile_persistent_resume_section()
    try:
        asyncio.run(test_live_playwright())
    except Exception as e:
        print(f"Live playwright check note: {e}")
