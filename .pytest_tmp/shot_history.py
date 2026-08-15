"""浏览器验证：历史会话完整显示（答复/委派/todo/审批决策）。"""
import asyncio
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8003"


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 960})
        await page.goto(f"{BASE}/chat/master-agent", wait_until="networkidle")
        await page.wait_for_timeout(2500)

        # 打开会话下拉（Select Session 按钮）
        await page.locator("button", has_text="Select Session").first.click()
        await page.wait_for_timeout(800)

        # 下拉项显示 session_id 前 8 位（verify-h）
        items = page.locator(".ant-dropdown-menu-item")
        count = await items.count()
        print("dropdown items:", count)
        for i in range(count):
            txt = (await items.nth(i).inner_text()).replace("\n", " / ")
            print(f"  [{i}] {txt[:100]}")

        def pick_verify(idx: int):
            return items.filter(has_text="verify-h").nth(idx)

        def scroll_top():
            # 消息容器是独立 overflowY:auto 区域，JS 直接置顶
            return page.evaluate("""() => {
              const el = [...document.querySelectorAll('div')].find(
                d => d.scrollHeight > d.clientHeight + 50
                  && getComputedStyle(d).overflowY === 'auto');
              if (el) el.scrollTop = 0;
            }""")

        # 会话 1：verify-history-5（委派 + todo，filter 后第 1 个）
        await pick_verify(1).click()
        await page.wait_for_timeout(2500)
        await scroll_top()
        await page.wait_for_timeout(600)
        await page.screenshot(
            path="verify_task40_05_history_delegation.png", full_page=False)

        # 会话 2：verify-history-6（审批决策徽标，filter 后第 0 个）
        await page.locator("button", has_text="verify-h").first.click()
        await page.wait_for_timeout(800)
        await pick_verify(0).click()
        await page.wait_for_timeout(2500)
        await scroll_top()
        await page.wait_for_timeout(600)
        await page.screenshot(
            path="verify_task40_06_history_approval.png", full_page=False)

        await browser.close()
    print("screenshots saved")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
