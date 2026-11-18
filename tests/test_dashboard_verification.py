import asyncio
import sys
import re
from playwright.async_api import async_playwright

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

async def main():
    with open("src/api/templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    # Verify each portal has an SSO card or status representation
    for portal in ["naukri", "bayt", "indeed", "cutshort", "wellfound"]:
        assert f'{portal}-session-pill' in html, f"Missing {portal}-session-pill"
        assert f'{portal}-auth-badge' in html, f"Missing {portal}-auth-badge"
        assert f'{portal}-sso-name' in html, f"Missing {portal}-sso-name"
        assert f'{portal}-sso-email' in html, f"Missing {portal}-sso-email"
        assert f'{portal}-sso-cookies' in html, f"Missing {portal}-sso-cookies"
        assert f'btn-{portal}-sso' in html, f"Missing btn-{portal}-sso"
        print(f"✅ Verified all SSO Hub card elements for '{portal}'")

    # Verify checkboxes
    for portal in ["naukri", "cutshort", "indeed", "wellfound"]:
        assert f'id="portal-{portal}"' in html, f"Missing portal-{portal} checkbox"
    print("✅ Found all portal checkboxes in HTML")

    # Verify filter
    assert 'id="ledger-filter-portal"' in html, "Missing ledger-filter-portal"
    print("✅ Found ledger-filter-portal in HTML")

    # Verify with live browser
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page_errors = []
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        await page.goto("http://localhost:8000", wait_until="networkidle")
        title = await page.title()
        print(f"Loaded page: {title}")

        # Switch to Portals Admin tab
        portals_tab_btn = await page.query_selector("button[data-tab='tab-admin-portals']")
        if portals_tab_btn:
            await portals_tab_btn.click()
            await page.wait_for_timeout(1000)
            print("✅ Clicked tab-admin-portals")

        # Check SSO buttons for Indeed, Cutshort, Wellfound, Naukri, Bayt
        for portal in ["naukri", "bayt", "indeed", "cutshort", "wellfound"]:
            login_btn = await page.query_selector(f"#btn-{portal}-sso")
            print(f"Portal '{portal}' SSO Login button present: {login_btn is not None}")
            assert login_btn is not None, f"Missing #btn-{portal}-sso in DOM"

        # Switch to Auto-Apply tab
        auto_apply_btn = await page.query_selector("button[data-tab='tab-auto-apply']")
        if auto_apply_btn:
            await auto_apply_btn.click()
            await page.wait_for_timeout(2000)
            print("✅ Clicked tab-auto-apply")

        # Verify ledger rendered rows
        rows = await page.query_selector_all("#applied-jobs-table-body tr")
        print(f"✅ Ledger rows rendered in UI: {len(rows)}")
        assert len(rows) > 0, "Ledger table should have rendered rows"

        assert len(page_errors) == 0, f"Page errors: {page_errors}"
        print("✅ Zero JS runtime errors across entire session!")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
