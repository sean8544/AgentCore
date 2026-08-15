"""诊断：打开 verify-h3 会话，dump 消息区 DOM 结构。"""
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
        target = None
        for i in range(n):
            txt = (await items.nth(i).inner_text()).strip()
            if "verify-h3" in txt:
                target = i
                break
        if target is None:
            print("未找到 verify-h3")
            await browser.close()
            return 1
        await items.nth(target).click()
        await page.wait_for_timeout(3000)

        # dump 消息区 HTML 特征
        info = await page.evaluate(
            """() => {
              const area = document.querySelector('.ant-layout-content') || document.body;
              const html = area.innerHTML;
              return {
                len: html.length,
                hasBorderLeft: html.includes('border-left'),
                hasToolCall: html.includes('write_todos') || html.includes('write_file'),
                hasTodoPanel: html.includes('ant-progress') || html.includes('进度'),
                msgCount: html.split('QwenPaw').length - 1,
                sample: html.slice(0, 1200),
              };
            }"""
        )
        print("len:", info["len"])
        print("hasBorderLeft:", info["hasBorderLeft"])
        print("hasToolCall:", info["hasToolCall"])
        print("hasTodoPanel:", info["hasTodoPanel"])
        print("msgCount:", info["msgCount"])
        print("sample:", info["sample"][:1000])

        await page.screenshot(path="verify_beauty_diag.png", full_page=False)
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
