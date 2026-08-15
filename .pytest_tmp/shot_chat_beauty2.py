"""浏览器验证：美化后的 工具调用卡片 / todo 面板 / 委派气泡。

遍历 master-agent 的会话历史，找到包含工具调用的会话后：
- 截图工具卡片（含展开 JSON 高亮）
- 截图 todo 面板（进度条）
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
        await page.wait_for_timeout(2000)

        session_btn = page.locator(
            'button:has-text("Session"), button:has-text("会话")'
        ).first
        found = False
        for attempt in range(10):
            if await session_btn.count() == 0:
                break
            await session_btn.click()
            await page.wait_for_timeout(600)
            items = page.locator(".ant-dropdown-menu-item")
            n = await items.count()
            if n == 0:
                break
            # 点第 attempt+1 个会话
            await items.nth(attempt).click()
            await page.wait_for_timeout(2500)
            cards = page.locator('code:has-text("write_todos"), code:has-text("write_file"), code:has-text("edit_file")')
            cnt = await cards.count()
            print(f"session[{attempt}] tool cards: {cnt}")
            if cnt > 0:
                # 展开第一个工具卡片的参数
                expanders = page.locator(".anticon-right, .anticon-down")
                await page.screenshot(path="verify_chat_beautified_2.png", full_page=False)
                # 点击第一张卡片标题展开
                first_card = page.locator("div[style*='border-left: 3px solid']").first
                if await first_card.count() > 0:
                    await first_card.click()
                    await page.wait_for_timeout(600)
                    await page.screenshot(path="verify_chat_beautified_3.png", full_page=False)
                found = True
                break

        # todo 面板（页面顶部）也截图
        await page.screenshot(path="verify_chat_beautified_4.png", full_page=False)
        await browser.close()
    print("验证:", "OK" if found else "未找到含工具调用的会话")
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
