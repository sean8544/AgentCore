"""细查 3002 审批按钮状态：disabled 属性 + 点击后的网络请求。"""
import asyncio
import time
import sys

from playwright.async_api import async_playwright

BASE = "http://localhost:3002"


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1500, "height": 960})
        reqs = []
        page.on("request", lambda r: reqs.append(f"REQ {r.method} {r.url}"))
        page.on("response", lambda r: reqs.append(f"RESP {r.status} {r.request.method} {r.url}"))
        await page.goto(f"{BASE}/chat/master-agent", wait_until="networkidle")
        await page.wait_for_timeout(3000)

        # 会话下拉信息（看 currentSessionId）
        sel = page.locator(".ant-dropdown-trigger, button").filter(has_text="会话").first
        print("session btn text:", (await sel.inner_text()).strip() if await sel.count() else "N/A")

        ta = page.locator("textarea.chat-textarea")
        await ta.fill("创建test文件 test2.txt")
        await page.keyboard.press("Enter")
        print("消息已发送")

        approve_btn = page.locator('button:has-text("Approve"), button:has-text("批准")')
        t0 = time.time()
        while time.time() - t0 < 60:
            await page.wait_for_timeout(2000)
            cnt = await approve_btn.count()
            print(f"[{time.time()-t0:.0f}s] approve_btns={cnt}")
            if cnt > 0:
                break

        # 打印每个审批按钮的状态
        n = await approve_btn.count()
        for i in range(n):
            b = approve_btn.nth(i)
            disabled = await b.get_attribute("disabled")
            txt = (await b.inner_text()).strip()
            print(f"  btn[{i}] text={txt!r} disabled={disabled}")

        # 打开会话下拉看 currentSessionId 显示
        session_btn = page.locator(
            'button:has-text("Session"), button:has-text("会话")'
        ).first
        if await session_btn.count():
            print("session header:", (await session_btn.inner_text()).strip())

        # 点击第一个可用审批按钮
        clicked = False
        for i in range(n):
            b = approve_btn.nth(i)
            if not await b.get_attribute("disabled"):
                await b.click()
                clicked = True
                print(f"clicked btn[{i}]")
                break
        if not clicked:
            print("所有审批按钮都 disabled")
        else:
            t1 = time.time()
            while time.time() - t1 < 30:
                await page.wait_for_timeout(2000)
                try:
                    alive = await page.evaluate("1+1")
                    err_banner = await page.locator(".ant-alert-error").count()
                    print(f"[{time.time()-t1:.0f}s] alive, error_banners={err_banner}")
                except Exception as e:
                    print(f"[{time.time()-t1:.0f}s] PAGE UNRESPONSIVE: {e}")
                if time.time() - t1 > 25:
                    break

        print("--- network log ---")
        for r in reqs:
            print("  ", r)
        await page.screenshot(path="repro3002_3.png", full_page=False)
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
