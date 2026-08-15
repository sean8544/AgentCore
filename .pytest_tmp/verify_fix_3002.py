"""修复验证：3002 新会话审批 → 检查 POST approval 发出且后端响应。"""
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
        await page.wait_for_timeout(2500)

        ta = page.locator("textarea.chat-textarea")
        await ta.fill("创建文件 fix-verify.txt")
        await page.keyboard.press("Enter")
        print("消息已发送")

        approve_btn = page.locator('button:has-text("Approve"), button:has-text("批准")')
        t0 = time.time()
        while time.time() - t0 < 90:
            await page.wait_for_timeout(2000)
            cnt = await approve_btn.count()
            print(f"[{time.time()-t0:.0f}s] approve_btns={cnt}")
            if cnt > 0:
                break

        n = await approve_btn.count()
        for i in range(n):
            b = approve_btn.nth(i)
            print(f"  btn[{i}] text={(await b.inner_text()).strip()!r} disabled={await b.get_attribute('disabled')}")

        # 会话下拉：修复后审批卡片出现时应显示具体会话 id
        sh = page.locator('button:has-text("会话"), button:has-text("Session")').first
        if await sh.count():
            print("session header:", (await sh.inner_text()).strip())

        clicked = False
        for i in range(n):
            b = approve_btn.nth(i)
            if not await b.get_attribute("disabled"):
                await b.click()
                clicked = True
                print(f"clicked btn[{i}]")
                break
        if not clicked:
            print("所有按钮 disabled")
            await browser.close()
            return 1

        # 观察 40 秒：POST approval 是否发出
        t1 = time.time()
        approval_seen = False
        while time.time() - t1 < 40:
            await page.wait_for_timeout(2000)
            for r in reqs:
                if "approval" in r and "POST" in r:
                    approval_seen = True
            try:
                alive = await page.evaluate("1+1")
                print(f"[{time.time()-t1:.0f}s] alive, approval_req={approval_seen}")
            except Exception as e:
                print(f"[{time.time()-t1:.0f}s] PAGE UNRESPONSIVE: {e}")
            if approval_seen and time.time() - t1 > 12:
                break

        print("--- approval 相关网络日志 ---")
        for r in reqs:
            if "approval" in r or "fix-verify" in r or ("chat" in r and "stream" in r):
                print("  ", r)
        await page.screenshot(path="verify_fix_3002.png", full_page=False)
        await browser.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
