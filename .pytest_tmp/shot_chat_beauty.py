"""浏览器验证：美化后的 工具调用卡片 / todo 面板 / 委派气泡。"""
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

        # 选择最近会话（若历史为空则直接截图）
        session_btn = page.locator(
            'button:has-text("Session"), button:has-text("会话")'
        ).first
        if await session_btn.count() > 0:
            await session_btn.click()
            await page.wait_for_timeout(800)
            items = page.locator(".ant-dropdown-menu-item")
            n = await items.count()
            print("session items:", n)
            if n > 0:
                await items.first.click()
                await page.wait_for_timeout(2500)

        await page.screenshot(path="verify_chat_beautified_1.png", full_page=False)

        # 统计工具调用卡片数量
        tool_cards = await page.locator("text=write_todos, text=write_file").count()
        print("tool-related rows:", tool_cards)
        await browser.close()
    print("验证完成（截图 verify_chat_beautified_1.png）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
