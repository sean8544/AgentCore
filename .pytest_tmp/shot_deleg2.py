"""补充验证：委派气泡视觉 + JSON 展开高亮。"""
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

        # 1) 滚动到委派气泡（黄色 #fffbe6）
        await page.evaluate(
            """() => {
              const els = [...document.querySelectorAll('div')];
              const bubble = els.find((el) => {
                const st = getComputedStyle(el);
                return st.backgroundColor === 'rgb(255, 251, 230)' && el.offsetHeight > 30;
              });
              if (bubble) bubble.scrollIntoView({ block: 'center' });
            }"""
        )
        await page.wait_for_timeout(800)
        await page.screenshot(path="verify_deleg_3_bubble.png", full_page=False)

        # 2) 展开一张 write_todos 卡片看 JSON 高亮
        cards = page.locator("div[style*='border-color']")
        cnt = await cards.count()
        print(f"cards: {cnt}")
        for i in range(min(cnt, 10)):
            c = cards.nth(i)
            txt = (await c.inner_text()).strip()
            if "write_todos" in txt:
                btn = c.locator("button")
                if await btn.count() > 0:
                    await btn.click()
                    await page.wait_for_timeout(500)
                    await c.scroll_into_view_if_needed()
                    await page.wait_for_timeout(400)
                    await page.screenshot(path="verify_deleg_4_json.png", full_page=False)
                    break

        await browser.close()
    print("验证: OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
