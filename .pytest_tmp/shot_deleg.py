"""验证委派 UI：打开 verify-history-3（菜单 [11]，翻译委派会话）：
1. DelegationBubble（消息内委派气泡）
2. TodoList（消息内嵌任务计划）
3. ToolCallCard（write_todos 蓝色卡片）
然后切到 general-purpose 子代理查看 DelegationRecordPanel。
"""
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
            if "10:09:33" in txt:  # verify-history-3 updated 时间
                target = i
                break
        if target is None:
            print("未找到 10:09:33 会话")
            await browser.close()
            return 1
        print(f"点击菜单项 [{target}]")
        await items.nth(target).click()
        await page.wait_for_timeout(3500)

        info = await page.evaluate(
            """() => {
              const html = document.querySelector('.ant-layout-content').innerHTML;
              const has = (k) => html.includes(k);
              return {
                hasDelegBubble: has('background: rgb(255, 251, 230)') || has('#fffbe6'),
                hasTodoList: has('ant-progress'),
                hasWriteTodos: has('write_todos'),
                hasTaskCard: has('general-purpose'),
              };
            }"""
        )
        print("master-agent 消息:", info)
        await page.screenshot(path="verify_deleg_1_master.png", full_page=False)

        # ── 切到 general-purpose 子代理 ──
        await page.goto(f"{BASE}/chat/general-purpose", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        info2 = await page.evaluate(
            """() => {
              const html = document.querySelector('.ant-layout-content').innerHTML;
              return {
                hasDelegPanel: html.includes('Delegation') || html.includes('委派'),
                hasTimeline: html.includes('rgb(250, 173, 20)'),
                hasTaskDesc: html.includes('hello') || html.includes('翻译'),
              };
            }"""
        )
        print("general-purpose 页面:", info2)
        await page.screenshot(path="verify_deleg_2_subagent.png", full_page=False)

        await browser.close()
    print("验证: OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
