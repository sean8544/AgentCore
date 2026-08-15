"""Take screenshots of AgentCore frontend for test report."""
import sys, time
from pathlib import Path

OUT = Path(r"d:\code\AgentCore")

def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        base = "http://127.0.0.1:8000"

        # 1. Home / Chat page
        print("[1] Chat page...")
        page.goto(f"{base}/chat", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)
        page.screenshot(path=str(OUT / "report_01_home.png"))

        # 2. Create an agent first
        print("[2] Creating agent for demo...")
        page.goto(f"{base}/agents", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "report_02_agents_page.png"))

        # 3. Navigate to Files page
        print("[3] Files page...")
        page.goto(f"{base}/files", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "report_03_files.png"))

        # 4. Models page
        print("[4] Models page...")
        page.goto(f"{base}/models", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "report_04_models.png"))

        # 5. Tools page
        print("[5] Tools page...")
        page.goto(f"{base}/tools", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "report_05_tools.png"))

        # 6. MCP page
        print("[6] MCP page...")
        page.goto(f"{base}/mcp", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "report_06_mcp.png"))

        # 7. Environments page
        print("[7] Environments page...")
        page.goto(f"{base}/environments", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "report_07_envs.png"))

        # 8. Agent Stats page
        print("[8] Agent Stats page...")
        page.goto(f"{base}/agent-stats", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "report_08_stats.png"))

        # 9. Chat with real interaction - send message and capture response
        print("[9] Chat interaction...")
        page.goto(f"{base}/chat", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)

        # Try to find chat input and send a message
        try:
            # Look for textarea or input
            textarea = page.locator("textarea").first
            if textarea.is_visible():
                textarea.click()
                textarea.fill("你好，请简单介绍一下你自己")
                page.wait_for_timeout(500)
                # Press Enter or find send button
                textarea.press("Enter")
                # Wait for response
                page.wait_for_timeout(15000)
                page.screenshot(path=str(OUT / "report_09_chat_reply.png"))
                print("    Chat reply captured!")
            else:
                page.screenshot(path=str(OUT / "report_09_chat_reply.png"))
                print("    No textarea found, captured current state")
        except Exception as e:
            page.screenshot(path=str(OUT / "report_09_chat_reply.png"))
            print(f"    Chat interaction error: {e}")

        # 10. Sessions page
        print("[10] Sessions page...")
        page.goto(f"{base}/sessions", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "report_10_sessions.png"))

        browser.close()
        print("\nAll screenshots saved to d:\\code\\AgentCore\\report_*.png")

if __name__ == "__main__":
    main()
