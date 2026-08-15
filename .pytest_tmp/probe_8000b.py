"""测试 8000 的 chat 端点响应时间 + 检查会话文件状态。"""
import json
import time
import urllib.request
import glob
import os

# 1) 会话文件
for f in sorted(glob.glob(r"d:\code\AgentCore\.agentcore\data\agents\master-agent\sessions\*.json"), key=os.path.getmtime, reverse=True)[:6]:
    st = os.stat(f)
    print(f"  {os.path.basename(f)} | mtime={time.strftime('%H:%M:%S', time.localtime(st.st_mtime))} | size={st.st_size}")

# 2) 8000 chat 端点测试
data = json.dumps({"message": "hi", "session_id": "port-test-8000"}).encode("utf-8")
t0 = time.time()
try:
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=75) as r:
        body = r.read().decode("utf-8")
    print(f"8000 chat OK in {time.time()-t0:.1f}s:", body[:200])
except Exception as e:
    print(f"8000 chat FAILED after {time.time()-t0:.1f}s: {type(e).__name__}: {e}")

# 3) 8000 models 配置
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/models", timeout=10) as r:
        print("8000 models:", r.read().decode("utf-8")[:400])
except Exception as e:
    print("8000 models ERR:", e)
