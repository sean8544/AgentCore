"""复现审批卡死：打开 7a2ac347 会话，点击 Approve，计时观察 UI 状态。"""
import asyncio
import time
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8005"
SID = "7a2ac347644949beadebc406c0ec10c1"


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1500, "height": 960})
        # 监听网络
        page.on("response", lambda r: print(
            f"[resp] {r.status} {r.request.method} {r.url}" if "approval" in r.url or "chat" in r.url else ""
        ))
        await page.goto(f"{BASE}/chat/master-agent", wait_until="networkidle")
        await page.wait_for_timeout(2500)

        # 打开会话下拉选择 7a2ac347
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
            if "7a2ac347" in txt:
                target = i
                break
        if target is None:
            # 找最新会话（第一项）
            print("未找到 7a2ac347，菜单项：")
            for i in range(min(n, 6)):
                print("  ", i, (await items.nth(i).inner_text()).strip()[:80])
            target = 0
        print(f"点击菜单项 [{target}]")
        await items.nth(target).click()
        await page.wait_for_timeout(3500)

        # 找审批按钮
        approve_btn = page.locator('button:has-text("Approve"), button:has-text("批准")')
        cnt = await approve_btn.count()
        print("approve buttons:", cnt)
        if cnt == 0:
            html = await page.evaluate("document.querySelector('.ant-layout-content').innerHTML")
            print("页面含 approvalNeeded:", "approvalNeeded" in html, "| write_file:", "write_file" in html)
            await page.screenshot(path="repro_1_no_approve.png", full_page=False)
            await browser.close()
            return 1

        t0 = time.time()
        await approve_btn.first.click()
        print(f"clicked at {t0:.1f}")

        # 每隔 3 秒检查 UI 是否响应
        for i in range(20):
            await page.wait_for_timeout(3000)
            elapsed = time.time() - t0
            try:
                # 页面是否响应（执行 JS 不卡）
                ok = await page.evaluate("1 + 1")
                # 检查按钮状态
                state = await page.evaluate(
                    """() => {
                      const btns = [...document.querySelectorAll('button')];
                      const send = btns.find((b) => b.innerText.includes('Stop') || b.innerText.includes('停止'));
                      const ta = document.querySelector('textarea');
                      return {
                        hasStopBtn: !!send,
                        textareaDisabled: ta ? ta.disabled : null,
                        spinCount: document.querySelectorAll('.ant-spin').length,
                      };
                    }"""
                )
                print(f"[{elapsed:.0f}s] page alive, state={state}")
            except Exception as e:
                print(f"[{elapsed:.0f}s] PAGE UNRESPONSIVE: {e}")
            if elapsed > 60:
                break

        await page.screenshot(path="repro_2_after60s.png", full_page=False)
        await browser.close()
    print("repro done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
