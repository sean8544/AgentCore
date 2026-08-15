"""端到端验证：会话记录完整性（答复/推理/tool_calls/todo/委派/审批持久化）。"""
import json
import sys

import httpx

BASE = "http://127.0.0.1:8003/api"
AGENT = "master-agent"


def stream_chat(message: str, session_id: str | None = None) -> dict:
    events = {
        "types": [], "todo": [], "subagents": 0,
        "interrupts": None, "final_text": "",
    }
    with httpx.Client(timeout=600) as client:
        with client.stream("POST", f"{BASE}/chat/stream", json={
            "agent_id": AGENT, "message": message, "session_id": session_id,
        }) as resp:
            current = ""
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("event: "):
                    current = line[7:].strip()
                    events["types"].append(current)
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    if current == "messages":
                        events["final_text"] += data.get("content", "")
                    elif current == "todo":
                        events["todo"].extend(data.get("todos", []))
                    elif current == "subagents":
                        events["subagents"] += 1
                    elif current == "interrupts":
                        events["interrupts"] = data.get("approval_request")
    return events


def history(session_id: str) -> list:
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{BASE}/chat/history",
                          params={"session_id": session_id, "limit": 200})
        resp.raise_for_status()
        return resp.json()


def main() -> int:
    ok = True

    # --- 1. 委派 + todo 回合（强制委派给 translater-agent） ---
    sid = "verify-history-5"
    ev = stream_chat(
        "请把翻译任务『把 hello 翻译成中文』委派给子代理 translater-agent 完成，"
        "同时用 write_todos 创建任务计划，最后汇总结果", sid)
    print("[1] SSE 事件类型:", ev["types"])
    print("    todo 数:", len(ev["todo"]), " subagents 事件:", ev["subagents"])
    print("    最终回复:", ev["final_text"][:80])

    msgs = history(sid)
    print("[2] 历史消息:", [(m["role"], len(m.get("content", "")),
                             sorted(m.keys())) for m in msgs])
    last = msgs[-1]
    assert last["role"] == "assistant", "最后一条应为 assistant"
    assert last["content"], "assistant 应有答复文本"
    print("[3] assistant 字段:", sorted(last.keys()))
    print("    tool_calls:", [tc["name"] for tc in last.get("tool_calls", [])])
    print("    todos:", [t.get("title") or t.get("content")
                         for t in last.get("todos", [])])
    print("    delegations:", last.get("delegations"))
    print("    reasoning:", (last.get("reasoning") or "")[:60])
    if last.get("todos"):
        print("    OK: todos 已持久化")
    else:
        ok = False
        print("    [FAIL] todos 缺失")
    if last.get("delegations"):
        print("    OK: 委派已持久化")
    if last.get("reasoning"):
        print("    OK: 推理已持久化")

    # --- 2. HITL 审批回合（循环审批直到完成） ---
    sid2 = "verify-history-6"
    ev2 = stream_chat(
        "请删除文件 /history_test2.txt（如文件不存在，请先创建它再删除）", sid2)
    print("[4] SSE:", ev2["types"])
    print("    interrupts:", ev2["interrupts"])
    if ev2["interrupts"]:
        print("    OK: 审批请求已触发")
        rounds = 0
        with httpx.Client(timeout=120) as client:
            while True:
                rounds += 1
                resp = client.post(
                    f"{BASE}/chat/master-agent/sessions/{sid2}/approval",
                    json={"decision": "approve"})
                body = resp.json()
                print("    approval 第", rounds, "轮:", resp.status_code,
                      (body.get("content") or body.get("status") or "")[:60])
                if body.get("status") != "pending_approval":
                    break
                if rounds > 4:
                    ok = False
                    print("    [FAIL] 审批轮数异常")
                    break
        msgs2 = history(sid2)
        print("[5] 审批会话历史:", [(m["role"], len(m.get("content", "")),
                                      sorted(m.keys())) for m in msgs2])
        decided = [m for m in msgs2 if m.get("approval")]
        if not decided:
            ok = False
            print("    [FAIL] 历史中没有审批决策记录")
        else:
            print("    OK: 历史中保留审批决策（", len(decided), "条）")
            last_dec = decided[-1]["approval"]
            print("    决策详情:", last_dec)
            if last_dec.get("tool_name"):
                print("    OK: 审批决策已回写（tool_name =",
                      last_dec["tool_name"], "）")
            else:
                ok = False
                print("    [FAIL] 审批决策 tool_name 为空")
            # 审批链完成：陈旧 pending 请求应被清除，只留决策徽标
            stale = [m for m in msgs2 if m.get("approval_request")
                     and not m.get("approval")]
            if stale:
                ok = False
                print("    [FAIL] 存在未决 approval_request:",
                      [m.get("approval_request") for m in stale])
            else:
                print("    OK: 审批链完成后无陈旧 pending 请求")
        if msgs2 and msgs2[-1]["role"] == "assistant" and msgs2[-1].get("content"):
            print("    OK: 审批后的最终答复已持久化")
        else:
            ok = False
            print("    [FAIL] 审批后的最终答复未持久化")
    else:
        print("    [WARN] 未触发审批（master-agent 可能未配置 delete 审批）")

    print("\n验证完成:", "ALL OK" if ok else "HAS ISSUES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
