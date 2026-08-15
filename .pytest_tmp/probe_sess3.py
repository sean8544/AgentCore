"""查看 8005 最新会话 7a2ac347 的消息内容，检查审批状态。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8005"

def get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

h = get(f"{BASE}/api/chat/history?session_id=7a2ac347644949beadebc406c0ec10c1&limit=50")
msgs = h if isinstance(h, list) else h.get("messages", [])
print("message count:", len(msgs))
for i, m in enumerate(msgs):
    print(f"--- [{i}] role={m.get('role')} id={m.get('id')} ts={m.get('timestamp')}")
    content = m.get("content") or ""
    print("    content:", content[:200].replace("\n", "\\n"))
    if m.get("approval_request"):
        print("    approval_request:", json.dumps(m["approval_request"], ensure_ascii=False)[:400])
    if m.get("approval"):
        print("    approval:", json.dumps(m["approval"], ensure_ascii=False)[:400])
    if m.get("tool_calls"):
        print("    tool_calls:", json.dumps(m["tool_calls"], ensure_ascii=False)[:300])
    if m.get("todos"):
        print("    todos:", json.dumps(m["todos"], ensure_ascii=False)[:200])
    if m.get("delegations"):
        print("    delegations:", json.dumps(m["delegations"], ensure_ascii=False)[:200])
