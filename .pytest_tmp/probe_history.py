"""查看 verify-history-3 的原始历史消息结构。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8005"

with urllib.request.urlopen(
    BASE + "/api/chat/history?session_id=verify-history-3&limit=50", timeout=15
) as r:
    msgs = json.loads(r.read().decode("utf-8"))

print(f"total messages: {len(msgs)}")
for i, m in enumerate(msgs):
    keys = list(m.keys())
    print(f"\n--- msg[{i}] role={m.get('role')} keys={keys}")
    print(f"  content: {str(m.get('content'))[:120]!r}")
    if m.get("tool_calls"):
        for tc in m["tool_calls"]:
            print(f"  tool_call: name={tc.get('name')} args={str(tc.get('args'))[:80]!r}")
    if m.get("todos"):
        print(f"  todos: {len(m['todos'])} items, first={json.dumps(m['todos'][0], ensure_ascii=False)[:150]}")
    if m.get("delegations"):
        print(f"  delegations: {json.dumps(m['delegations'], ensure_ascii=False)[:200]}")
    if m.get("approval_request"):
        print(f"  approval_request: {json.dumps(m['approval_request'], ensure_ascii=False)[:150]}")
    if m.get("approval"):
        print(f"  approval: {json.dumps(m['approval'], ensure_ascii=False)[:150]}")
