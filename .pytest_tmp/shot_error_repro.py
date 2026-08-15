"""浏览器验证：模型 429 时聊天页显示错误提示（不再静默失败）。"""
import asyncio
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8005"


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 960})
        await page.goto(f"{BASE}/chat/master-agent", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        await page.locator("textarea.chat-textarea").fill(
            "请创建文件 error_demo.txt，内容为 hello world")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(15000)

        # 错误 Alert（.ant-alert-error）应出现
        alert_count = await page.locator(".ant-alert-error").count()
        print("错误提示 Alert 数量:", alert_count)
        if alert_count > 0:
            text = await page.locator(".ant-alert-error").first.inner_text()
            print("Alert 内容:", text[:120])
            await page.screenshot(path="verify_stream_error_shown.png", full_page=False)
            ok = "429" in text or "配额" in text or "Stream error" in text
        else:
            # 消息气泡上的失败标记
            fail_badges = await page.locator(".ant-typography-danger").count()
            print("失败标记数量:", fail_badges)
            await page.screenshot(path="verify_stream_error_shown.png", full_page=False)
            ok = fail_badges > 0

        await browser.close()
    print("验证:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
