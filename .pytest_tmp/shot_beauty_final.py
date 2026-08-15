"""浏览器验证：打开 verify-history-5 会话，截图美化后的：
1. 顶部 todo 面板（TodoPanel 进度条）
2. 委派记录面板（DelegationRecordPanel 时间线）
3. 工具调用卡片（ToolCallCard 彩色图标 + 可展开 JSON 高亮）
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

        # 打开会话下拉
        session_btn = page.locator(
            'button:has-text("Session"), button:has-text("会话")'
        ).first
        await session_btn.click()
        await page.wait_for_timeout(600)
        items = page.locator(".ant-dropdown-menu-item")
        n = await items.count()
        print(f"dropdown items: {n}")

        # 找含 todos/delegations 的会话（verify-history-3，前 8 位 verify-h3）
        target = None
        for i in range(n):
            txt = (await items.nth(i).inner_text()).strip()
            print(f"  [{i}] {txt[:50]}")
            if "verify-h3" in txt:
                target = i
        if target is None:
            await browser.close()
            print("未找到 verify-history-3")
            return 1

        await items.nth(target).click()
        await page.wait_for_timeout(3000)

        # 1) 顶部 todo 面板 + 委派记录（整页截图）
        await page.screenshot(path="verify_beauty_todo_panel.png", full_page=False)

        # 2) 工具卡片区域：滚动到消息区底部，截图
        await page.evaluate(
            "() => { const el = document.querySelector('.ant-layout-content') || document.body; el.scrollTop = el.scrollHeight; }"
        )
        await page.wait_for_timeout(800)
        await page.screenshot(path="verify_beauty_toolcards.png", full_page=False)

        # 3) 展开第一张工具卡片的 JSON 参数
        cards = page.locator("div[style*='border-left: 3px solid']")
        cnt = await cards.count()
        print(f"tool cards on page: {cnt}")
        if cnt > 0:
            # 点击卡片标题区（含箭头按钮）
            btn = cards.first.locator("button")
            if await btn.count() > 0:
                await btn.click()
                await page.wait_for_timeout(600)
            await page.screenshot(path="verify_beauty_json_expanded.png", full_page=False)

        await browser.close()
    print("验证: OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
