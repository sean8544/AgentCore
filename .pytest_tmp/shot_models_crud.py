"""浏览器验证：模型管理页 新增/测试连接/设为默认 全流程。"""
import asyncio
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8005"

ZH = True  # 默认语言猜测为中文；失败时切英文重试


async def _fill_and_verify(page, zh: bool) -> str:
    """执行新增→测试连接→保存→设为默认；返回 '' 或错误信息。"""
    add_btn = page.locator(
        'button:has-text("新增模型"), button:has-text("Add Model")'
    )
    await add_btn.first.click()
    await page.wait_for_timeout(600)

    # 基础字段
    await page.locator('input[placeholder*="qwen3.6-plus"]').first.fill("verify-model")
    await page.locator('input[placeholder*="openai.azure.com"]').first.fill(
        "https://example.invalid/v1"
    )
    key_input = page.locator(
        'input[placeholder*="密钥"], input[placeholder*="API key"]'
    ).first
    await key_input.fill("sk-demo-verify")

    # 进阶配置：headers + extra_body
    coll_header = page.locator(
        'div.ant-collapse-header:has-text("进阶配置"), div.ant-collapse-header:has-text("Advanced")'
    ).first
    await coll_header.click()
    await page.wait_for_timeout(400)
    add_hdr = page.locator(
        'button:has-text("+ Add Header"), button:has-text("添加 Header")'
    ).first
    await add_hdr.click()
    await page.wait_for_timeout(300)
    await page.locator('input[placeholder*="Header"]').first.fill("x-app")
    await page.locator('input[placeholder="Value"]').first.fill("cli")
    await page.locator('textarea[placeholder*="extra_body"]').first.fill(
        '{"enable_thinking": false, "max_tokens": 2048}'
    )

    # 测试连接 → 应显示失败 Alert（example.invalid 不可达）
    test_btn = page.locator(
        'button:has-text("测试连接"), button:has-text("Test Connection")'
    ).first
    await test_btn.click()
    await page.wait_for_timeout(6000)
    alert_err = page.locator(".ant-alert-error").count()
    print(f"  [test] error alert count = {alert_err}")

    # 保存
    save_btn = page.locator('button:has-text("保存"), button:has-text("Save")').last
    await save_btn.click()
    await page.wait_for_timeout(1500)
    row = page.locator('tr:has-text("verify-model")')
    if await row.count() == 0:
        return "表格未出现 verify-model"
    print("  [save] row visible")

    # 设为默认
    default_btn = page.locator(
        'button:has-text("设为默认"), button:has-text("Set Default")'
    ).first
    await default_btn.click()
    await page.wait_for_timeout(1500)
    gold_tag = page.locator('.ant-tag-gold:has-text("默认"), .ant-tag-gold:has-text("Default")')
    if await gold_tag.count() == 0:
        return "未出现默认 Tag"
    print("  [default] gold tag visible")

    await page.screenshot(path="verify_models_page.png", full_page=False)
    return ""


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1500, "height": 960})
        await page.goto(f"{BASE}/models", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # 语言探测：标题按钮存在则直接跑
        zh_btn = await page.locator('button:has-text("新增模型")').count()
        en_btn = await page.locator('button:has-text("Add Model")').count()
        print(f"language buttons: zh={zh_btn} en={en_btn}")
        await page.screenshot(path="verify_models_empty.png", full_page=False)

        err = await _fill_and_verify(page, zh_btn > 0)
        await browser.close()

    print("验证:", "OK" if not err else f"FAIL: {err}")
    return 0 if not err else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
