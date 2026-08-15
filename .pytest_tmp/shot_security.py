"""浏览器验证：安全配置页 + 聊天页停止按钮。"""
import asyncio
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8004"


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 960})

        # 1. 安全配置页
        await page.goto(f"{BASE}/security", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="verify_security_page.png", full_page=False)

        # 打开开关并选择工具（antd v6 类名）
        await page.locator(".ant-switch").first.click()
        await page.wait_for_timeout(400)
        await page.locator(".ant-card .ant-select").first.click()
        await page.wait_for_timeout(800)
        await page.locator(".ant-select-item-option", has_text="delete").first.click()
        await page.wait_for_timeout(400)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        await page.screenshot(path="verify_security_page_configured.png", full_page=False)
        await page.locator("button", has_text="保存配置").or_(
            page.locator("button", has_text="Save Settings")
        ).first.click()
        await page.wait_for_timeout(1500)

        # 2. 聊天页：发送长回复请求 → 点停止按钮
        await page.goto(f"{BASE}/chat/master-agent", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        await page.locator("textarea.chat-textarea").fill(
            "请写一篇 800 字的关于人工智能未来发展的报告，不要使用任何工具")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(4000)
        # 流式中 → 停止按钮应可见（红色 danger）
        stop_visible = await page.locator(".ant-btn-dangerous").count()
        print("停止按钮可见:", stop_visible > 0)
        if stop_visible > 0:
            await page.locator(".ant-btn-dangerous").first.click()
            await page.wait_for_timeout(1500)
            await page.screenshot(path="verify_chat_stopped.png", full_page=False)
        else:
            # 流式太快结束——至少截图当前状态
            await page.screenshot(path="verify_chat_stopped.png", full_page=False)

        await browser.close()
    print("screenshots saved")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
