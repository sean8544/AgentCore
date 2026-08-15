"""探测 8000 的数据目录位置：通过 agents API 暴露的路径特征判断。
并列出 8000 的所有会话（不限 agent）按时间排序。"""
import json
import urllib.request

def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"__err__": str(e)}

# 8000 所有会话
sess = get("http://127.0.0.1:8000/api/chat/sessions")
items = sess if isinstance(sess, list) else sess.get("sessions", [])
print("8000 total:", len(items))
items = sorted(items, key=lambda s: s.get("updated_at", ""), reverse=True)
for s in items[:12]:
    print("  -", s.get("session_id"), "| agent:", s.get("agent_id"), "| msgs:", s.get("message_count"), "| updated:", s.get("updated_at"))

# 试探 8000 的 agent 是否有内核文件（暴露工作目录）
a = get("http://127.0.0.1:8000/api/agents")
print("agents:", a)
