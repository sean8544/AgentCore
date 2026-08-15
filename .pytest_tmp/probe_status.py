"""对比 8000/8005 对 7a2ac347 的 approval/status（只读探测 graph 状态）。"""
import json
import urllib.request

SID = "7a2ac347644949beadebc406c0ec10c1"

for port in (8000, 8005):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/chat/master-agent/sessions/{SID}/approval/status", timeout=12
        ) as r:
            print(port, "status:", r.read().decode("utf-8")[:300])
    except Exception as e:
        print(port, "status ERR:", type(e).__name__, e)
