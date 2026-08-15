"""查看 8000 的 8af2d17b 会话内容 + 直接 POST 审批测试 8000 端点行为。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"

def get(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"__err__": str(e)}

h = get(f"{BASE}/api/chat/history?session_id=8af2d17be4ec42048b5c298957b90757&limit=50")
msgs = h if isinstance(h, list) else h.get("messages", [])
print("8000 8af2d17b messages:", len(msgs))
for m in msgs:
    print("  role:", m.get("role"), "| approval_request:", "YES" if m.get("approval_request") else "no", "| content:", (m.get("content") or "")[:100].replace("\n", "\\n"))

# 直接 POST 审批测试（如果该会话有待审批请求）
if msgs and any(m.get("approval_request") for m in msgs):
    data = json.dumps({"decision": "approve"}).encode("utf-8")
    t0 = time.time()
    try:
        req = urllib.request.Request(
            f"{BASE}/api/chat/master-agent/sessions/8af2d17be4ec42048b5c298957b90757/approval",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            body = r.read().decode("utf-8")
        print(f"POST approval OK in {time.time()-t0:.1f}s:", body[:300])
    except Exception as e:
        print(f"POST approval FAILED after {time.time()-t0:.1f}s:", e)
else:
    print("8af2d17b 无 pending approval")
