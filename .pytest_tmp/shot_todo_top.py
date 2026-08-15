"""验证 TodoPanel：打开 bd1df2e7，滚动消息区到顶部截图。"""
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
        await items.nth(0).click()
        await page.wait_for_timeout(3500)

        # 滚动消息区到顶部
        await page.evaluate(
            """() => {
              const els = document.querySelectorAll('[style*="overflow-y: auto"]');
              for (const el of els) el.scrollTop = 0;
            }"""
        )
        await page.wait_for_timeout(600)
        await page.screenshot(path="verify_final_4_todo_top.png", full_page=False)

        # 检查页面特征
        info = await page.evaluate(
            """() => {
              const html = document.querySelector('.ant-layout-content').innerHTML;
              return {
                hasProgress: html.includes('ant-progress'),
                hasTaskPlan: html.includes('Task Plan') || html.includes('任务计划'),
                hasDelegation: html.includes('Delegation') || html.includes('委派'),
                qwenCount: html.split('QwenPaw').length - 1,
                hasTimeline: html.includes('background: rgb(250, 173, 20)'),
              };
            }"""
        )
        print(info)
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
