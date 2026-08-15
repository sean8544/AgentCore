"""复现后复查 7a2ac347 状态：审批决策是否已记录、approval_request 是否清除。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8005"

def get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

h = get(f"{BASE}/api/chat/history?session_id=7a2ac347644949beadebc406c0ec10c1&limit=50")
msgs = h if isinstance(h, list) else h.get("messages", [])
for i, m in enumerate(msgs):
    print(f"[{i}] role={m.get('role')} approval_request={'YES' if m.get('approval_request') else 'no'} approval={json.dumps(m.get('approval'), ensure_ascii=False) if m.get('approval') else 'no'}")
    print("   content:", (m.get("content") or "")[:120].replace("\n", "\\n"))
