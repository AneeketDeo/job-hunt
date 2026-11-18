import asyncio
import sys
from pathlib import Path
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
from playwright.async_api import async_playwright
from src.automation.browser_factory import STEALTH_EVASION_SCRIPT, CHROME_LAUNCH_ARGS

async def test_indeed():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=CHROME_LAUNCH_ARGS)
        state_file = Path("sessions/indeed_state.json")
        ctx_kwargs = {"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
        if state_file.exists():
            ctx_kwargs["storage_state"] = str(state_file)
        ctx = await b.new_context(**ctx_kwargs)
        await ctx.add_init_script(STEALTH_EVASION_SCRIPT)
        page = await ctx.new_page()
        
        url = "https://in.indeed.com/jobs?q=GenAI+developer&l=Pune"
        print(f"Navigating to {url}...")
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        print("HTTP Status:", resp.status if resp else "None")
        title = await page.title()
        print("Page Title:", title)
        await asyncio.sleep(2)
        
        cards = await page.query_selector_all("div.job_seen_beacon, [data-testid='jobCard'], .jobsearch-ResultsList > li, a.jcs-JobTitle")
        print("Cards / links found:", len(cards))
        
        # Check title selectors
        for sel in ["h2.jobTitle span", "[data-testid='jobTitle']", "a.jcs-JobTitle", "h2.jobTitle a"]:
            found = await page.query_selector_all(sel)
            if found:
                print(f"Selector '{sel}' found: {len(found)}")
                first_text = await found[0].inner_text()
                print(f" First text: {first_text}")
        
        await b.close()

if __name__ == "__main__":
    asyncio.run(test_indeed())
