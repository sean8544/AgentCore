"""ä¿®å¤éªŒè¯ï¼š3002 æ–°ä¼šè¯å®¡æ‰¹ â†’ æ£€æŸ¥ POST approval å‘å‡ºä¸”åŽç«¯å“åº”ã€‚"""
import asyncio
import time
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8005"


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
        await ta.fill("åˆ›å»ºæ–‡ä»¶ fix-verify8005.txt")
        await page.keyboard.press("Enter")
        print("æ¶ˆæ¯å·²å‘é€")

        approve_btn = page.locator('button:has-text("Approve"), button:has-text("æ‰¹å‡†")')
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

        # ä¼šè¯ä¸‹æ‹‰ï¼šä¿®å¤åŽå®¡æ‰¹å¡ç‰‡å‡ºçŽ°æ—¶åº”æ˜¾ç¤ºå…·ä½“ä¼šè¯ id
        sh = page.locator('button:has-text("ä¼šè¯"), button:has-text("Session")').first
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
            print("æ‰€æœ‰æŒ‰é’® disabled")
            await browser.close()
            return 1

        # è§‚å¯Ÿ 40 ç§’ï¼šPOST approval æ˜¯å¦å‘å‡º
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

        print("--- approval ç›¸å…³ç½‘ç»œæ—¥å¿— ---")
        for r in reqs:
            if "approval" in r or "fix-verify" in r or ("chat" in r and "stream" in r):
                print("  ", r)
        await page.screenshot(path="verify_fix_8005.png", full_page=False)
        await browser.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

