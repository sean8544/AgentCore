"""验证 8b66dbf2 审批结果：approval 记录 + 文件创建。"""
import json
import urllib.request
import os
import time

BASE = "http://127.0.0.1:8000"

def get(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"__err__": str(e)}

h = get(f"{BASE}/api/chat/history?session_id=8b66dbf2551947f590bea7c796320712&limit=50")
msgs = h if isinstance(h, list) else h.get("messages", [])
print("messages:", len(msgs))
for m in msgs:
    print(f"  role={m.get('role')} | approval_request={'YES' if m.get('approval_request') else 'no'} | approval={json.dumps(m.get('approval'), ensure_ascii=False) if m.get('approval') else 'no'}")
    print("    content:", (m.get("content") or "")[:150].replace("\n", "\\n"))

# fix-verify.txt 是否创建（8000 与 8005 共享 workspace）
p = r"d:\code\AgentCore\.agentcore\workspace\agent\master-agent\fix-verify.txt"
print("fix-verify.txt exists:", os.path.exists(p), "| mtime:", time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(p))) if os.path.exists(p) else "-")
