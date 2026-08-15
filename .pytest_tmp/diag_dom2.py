"""诊断2：dump 消息区完整 HTML 到文件，分析渲染的组件结构。"""
import asyncio
import sys
from pathlib import Path

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
            if "verify-h3" in txt:
                await items.nth(i).click()
                break
        await page.wait_for_timeout(3000)

        html = await page.evaluate(
            """() => {
              const area = document.querySelector('.ant-layout-content') || document.body;
              return area.innerHTML;
            }"""
        )
        Path("d:/code/AgentCore/.pytest_tmp/diag_html.html").write_text(
            html, encoding="utf-8"
        )
        print("saved, len:", len(html))
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
