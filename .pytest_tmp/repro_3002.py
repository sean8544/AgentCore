"""在 3002（vite dev → 8000）页面复现：发消息 + 审批，观察是否卡死。"""
import asyncio
import time
import sys

from playwright.async_api import async_playwright

BASE = "http://localhost:3002"


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1500, "height": 960})
        page.on("response", lambda r: print(
            f"[resp] {r.status} {r.request.method} {r.url}" if "approval" in r.url or "/api/chat" in r.url else ""
        ))
        await page.goto(f"{BASE}/chat/master-agent", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        print("title:", await page.title())
        # 页面是否渲染
        has_input = await page.locator("textarea").count()
        print("has textarea:", has_input)
        if has_input == 0:
            await page.screenshot(path="repro3002_0.png", full_page=False)
            print("页面可能未渲染（vite 编译失败？）")
            await browser.close()
            return 1

        # 新会话发消息
        new_btn = page.locator('button:has-text("New"), button:has-text("新建")')
        if await new_btn.count() > 0:
            await new_btn.first.click()
            await page.wait_for_timeout(1000)

        ta = page.locator("textarea.chat-textarea")
        await ta.fill("创建test文件 test2.txt")
        await page.keyboard.press("Enter")
        print("消息已发送，等待 agent 回复...")

        # 等审批按钮出现（最多 90 秒）
        approve_btn = page.locator('button:has-text("Approve"), button:has-text("批准")')
        t0 = time.time()
        while time.time() - t0 < 90:
            await page.wait_for_timeout(3000)
            try:
                alive = await page.evaluate("1+1")
                cnt = await approve_btn.count()
                print(f"[{time.time()-t0:.0f}s] alive, approve_btns={cnt}")
                if cnt > 0:
                    break
            except Exception as e:
                print(f"[{time.time()-t0:.0f}s] PAGE UNRESPONSIVE: {e}")
        if await approve_btn.count() == 0:
            await page.screenshot(path="repro3002_1_noapprove.png", full_page=False)
            print("未出现审批按钮")
            await browser.close()
            return 1

        t1 = time.time()
        await approve_btn.first.click()
        print(f"点击审批 at {time.time()-t1:.0f}s")
        # 观察 45 秒
        for i in range(15):
            await page.wait_for_timeout(3000)
            el = time.time() - t1
            try:
                state = await page.evaluate(
                    """() => {
                      const ta = document.querySelector('textarea');
                      return { disabled: ta ? ta.disabled : null, spin: document.querySelectorAll('.ant-spin').length };
                    }"""
                )
                print(f"[{el:.0f}s] alive, {state}")
            except Exception as e:
                print(f"[{el:.0f}s] PAGE UNRESPONSIVE: {e}")
            if el > 45:
                break
        await page.screenshot(path="repro3002_2_after45s.png", full_page=False)
        await browser.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
