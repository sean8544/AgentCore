"""探测后端会话数据：找出包含 tool_calls / todos / delegations 的会话。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8005"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> None:
    sessions = get("/api/chat/sessions?agent_id=master-agent")
    print(f"total sessions: {len(sessions)}")
    for s in sessions[:20]:
        sid = s.get("session_id") or s.get("id")
        title = (s.get("title") or s.get("summary") or "")[:40]
        msgs = get(f"/api/chat/history?session_id={sid}&limit=50")
        has_tc = has_todo = has_del = 0
        for m in msgs:
            if m.get("tool_calls") or m.get("toolCalls"):
                has_tc += len(m.get("tool_calls") or m.get("toolCalls") or [])
            todos = m.get("todos")
            if todos:
                has_todo += len(todos)
            if m.get("delegations"):
                has_del += len(m["delegations"])
        flag = ""
        if has_tc:
            flag += f" [toolCalls={has_tc}]"
        if has_todo:
            flag += f" [todos={has_todo}]"
        if has_del:
            flag += f" [delegations={has_del}]"
        print(f"{sid} | {title}{flag}")


if __name__ == "__main__":
    main()
