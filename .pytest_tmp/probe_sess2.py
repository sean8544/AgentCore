"""检查 8000/8005 的 agents、sessions，找出最近审批的会话状态。"""
import json
import urllib.request

def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"__err__": str(e)}

for port in (8000, 8005):
    print(f"===== port {port} =====")
    agents = get(f"http://127.0.0.1:{port}/api/agents")
    if isinstance(agents, dict) and agents.get("__err__"):
        print("agents ERR:", agents["__err__"])
        continue
    print("agents:", [a.get("agent_id") if isinstance(a, dict) else a for a in agents][:10] if isinstance(agents, list) else agents)
    sess = get(f"http://127.0.0.1:{port}/api/chat/sessions")
    if isinstance(sess, dict) and sess.get("__err__"):
        print("sessions ERR:", sess["__err__"])
        continue
    items = sess if isinstance(sess, list) else sess.get("sessions", [])
    items = sorted(items, key=lambda s: s.get("updated_at", ""), reverse=True)
    print("total sessions:", len(items))
    for s in items[:5]:
        print("  -", s.get("session_id"), "| agent:", s.get("agent_id"), "| msgs:", s.get("message_count"), "| updated:", s.get("updated_at"))
