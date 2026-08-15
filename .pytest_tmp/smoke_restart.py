import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1400, "height": 900})
        await pg.goto("http://127.0.0.1:8000/", wait_until="networkidle")
        await pg.wait_for_timeout(1500)
        title = await pg.title()
        print("TITLE:", title)
        body = (await pg.inner_text("body"))[:300]
        print("BODY:", body.replace(chr(10), " | ")[:300])
        await pg.screenshot(path="verify_restart_8000.png")
        await b.close()

asyncio.run(main())
