"""对比 8005 返回的 JS 与本地 dist JS 的差异。"""
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8005"

with urllib.request.urlopen(BASE + "/assets/index-zUYdQBQP.js", timeout=30) as r:
    body = r.read()

local = (Path("d:/code/AgentCore/console/dist/assets/index-zUYdQBQP.js")).read_bytes()
print("server bytes:", len(body), "local bytes:", len(local))
print("equal:", body == local)
print("encoding:", r.headers.get("Content-Encoding"), r.headers.get("Content-Length"))
# 检查服务器 JS 中的特征
for key in [b"border-left", b"STATUS_META", b"DelegationRecordPanel", b"Greeting", b"Task Plan"]:
    print(key.decode(), "in server:", key in body, "| in local:", key in local)
