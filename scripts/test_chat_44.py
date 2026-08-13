"""Task #44: end-to-end verify POST /api/chat after backend restart."""
import uuid

import httpx

base = "http://127.0.0.1:8000"
session_id = f"task44-{uuid.uuid4().hex[:8]}"

health = httpx.get(f"{base}/api/health", timeout=10)
print("health:", health.status_code, health.text[:120])

r = httpx.post(
    f"{base}/api/chat",
    json={"agent_id": "default", "message": "你好", "session_id": session_id},
    timeout=120,
)
print("chat status:", r.status_code)
data = r.json()
print("session_id:", data.get("session_id"))
print("reply:", str(data.get("content"))[:300])
