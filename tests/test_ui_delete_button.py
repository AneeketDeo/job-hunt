import asyncio
import sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
from playwright.async_api import async_playwright

async def verify_ui_delete():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto("http://localhost:8000", wait_until="networkidle")

        # 1. Click Auto-Apply tab
        auto_apply_btn = await page.query_selector("button[data-tab='tab-auto-apply']")
        if auto_apply_btn:
            await auto_apply_btn.click()
            await page.wait_for_timeout(1500)

        # 2. Check header select-all box
        select_all = await page.query_selector("#ledger-select-all")
        assert select_all is not None, "Missing #ledger-select-all in table thead"
        print("✅ Found #ledger-select-all checkbox in header")

        # 3. Check row checkboxes
        row_checkboxes = await page.query_selector_all(".ledger-job-checkbox")
        print(f"✅ Found {len(row_checkboxes)} row checkboxes in ledger table")
        assert len(row_checkboxes) > 0, "Expected ledger row checkboxes"

        # 4. Check trash can buttons on rows
        trash_buttons = await page.query_selector_all("#applied-jobs-table-body button i.fa-trash-can")
        print(f"✅ Found {len(trash_buttons)} trash can buttons on rows")
        assert len(trash_buttons) > 0, "Expected trash buttons on each row"

        # 5. Check bulk delete button initial state (hidden)
        delete_btn = await page.query_selector("#btn-delete-selected")
        assert delete_btn is not None, "Missing #btn-delete-selected in toolbar"
        is_hidden = await delete_btn.is_hidden()
        print(f"✅ Bulk delete button initially hidden: {is_hidden}")

        # 6. Check first row checkbox -> should unhide bulk delete button
        await row_checkboxes[0].check()
        await page.wait_for_timeout(300)
        is_visible_after_check = await delete_btn.is_visible()
        count_text = await page.inner_text("#delete-selected-count")
        print(f"✅ Bulk delete button visible after check: {is_visible_after_check}, count: {count_text}")
        assert is_visible_after_check, "Bulk delete button should be visible when row is checked"
        assert count_text == "1", f"Expected count 1 but got {count_text}"

        # 7. Check 'Select All' in header
        await select_all.check()
        await page.wait_for_timeout(300)
        count_all_text = await page.inner_text("#delete-selected-count")
        print(f"✅ After Select All, count text: {count_all_text} (total: {len(row_checkboxes)})")
        assert count_all_text == str(len(row_checkboxes))

        # 8. Uncheck 'Select All'
        await select_all.uncheck()
        await page.wait_for_timeout(300)
        is_hidden_again = await delete_btn.is_hidden()
        print(f"✅ Bulk delete button hidden after unchecking all: {is_hidden_again}")
        assert is_hidden_again, "Bulk delete button should be hidden after unchecking"

        assert len(errors) == 0, f"Encountered JS errors: {errors}"
        print("✅ Zero JS errors during UI delete interaction tests!")

        await browser.close()
        print("\n🎉 ALL UI DELETE INTERACTION TESTS PASSED!")

if __name__ == "__main__":
    asyncio.run(verify_ui_delete())
