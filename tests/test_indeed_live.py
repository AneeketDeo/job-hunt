import asyncio
import sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
from playwright.async_api import async_playwright

async def test_indeed():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = await b.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        page = await ctx.new_page()
        resp = await page.goto("https://in.indeed.com/jobs?q=GenAI+developer&l=Pune", wait_until="domcontentloaded")
        print("HTTP Status:", resp.status if resp else "No resp")
        title = await page.title()
        print("Page Title:", title)
        cards = await page.query_selector_all("div.job_seen_beacon, [data-testid='jobCard'], .jobsearch-ResultsList > li")
        print("Cards found:", len(cards))
        content = await page.content()
        print("Content length:", len(content))
        if "cloudflare" in content.lower() or "challenge" in content.lower():
            print("⚠️ Cloudflare challenge detected!")
        await b.close()

if __name__ == "__main__":
    asyncio.run(test_indeed())
