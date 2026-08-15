"""最终验证：打开 bd1df2e7 会话（问候会话，含 25 toolCalls + 8 todos），
截图：todo 面板 / 工具卡片 / 展开 JSON 高亮 / 委派气泡。
"""
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
        # 第 0 项 = bd1df2e7（问候会话）
        await items.nth(0).click()
        await page.wait_for_timeout(3500)

        # 页面顶部：todo 面板 + 委派记录面板
        await page.screenshot(path="verify_final_1_todo_panel.png", full_page=False)

        # 滚动消息区到底部（工具卡片集中区）
        await page.evaluate(
            """() => {
              const el = document.querySelector('[style*="overflow-y: auto"]');
              if (el) el.scrollTop = el.scrollHeight;
            }"""
        )
        await page.wait_for_timeout(800)
        await page.screenshot(path="verify_final_2_toolcards.png", full_page=False)

        # 展开第一张 write_todos 卡片的 JSON
        cards = page.locator("div[style*='border-color']")
        cnt = await cards.count()
        print(f"tool cards: {cnt}")
        expanded = False
        for i in range(min(cnt, 8)):
            c = cards.nth(i)
            txt = (await c.inner_text()).strip()
            if "write_todos" in txt:
                btn = c.locator("button")
                if await btn.count() > 0:
                    await btn.click()
                    await page.wait_for_timeout(500)
                    expanded = True
                    break
        await page.screenshot(path="verify_final_3_json_expanded.png", full_page=False)
        print("expanded:", expanded)

        await browser.close()
    print("验证: OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
