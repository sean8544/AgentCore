"""验证 JSON 高亮：展开 write_todos 卡片后检查彩色 span。"""
import asyncio
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8005"


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1500, "height": 960})
        await page.goto(f"{BASE}/chat/master-agent", wait_until="networkidle")
        await page.wait_for_timeout(2500)

        session_btn = page.locator(
            'button:has-text("Session"), button:has-text("会话")'
        ).first
        await session_btn.click()
        await page.wait_for_timeout(600)
        items = page.locator(".ant-dropdown-menu-item")
        n = await items.count()
        for i in range(n):
            txt = (await items.nth(i).inner_text()).strip()
            if "10:09:33" in txt:
                await items.nth(i).click()
                break
        await page.wait_for_timeout(3500)

        cards = page.locator("div[style*='border-color']")
        cnt = await cards.count()
        for i in range(min(cnt, 10)):
            c = cards.nth(i)
            txt = (await c.inner_text()).strip()
            if "write_todos" in txt:
                btn = c.locator("button")
                if await btn.count() > 0:
                    await btn.click()
                    await page.wait_for_timeout(600)
                    break

        info = await page.evaluate(
            """() => {
              const pre = document.querySelector('pre');
              if (!pre) return { found: false };
              const spans = [...pre.querySelectorAll('span[style*="color"]')];
              const colors = [...new Set(spans.map((s) => s.style.color))];
              return { found: true, spanCount: spans.length, colors };
            }"""
        )
        print(info)
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
