"""端到端验证：全局安全配置（危险操作审批）+ 停止对话。

1. 全局审批配置 PUT 后对所有 agent 生效（新 agent 无 agent 级规则也触发审批）
2. SSE 流中途断开 = 停止对话：后端停止执行并保留部分回复
"""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8004/api"
AGENT = "sec-global"


def history(session_id: str) -> list:
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{BASE}/chat/history",
            params={"session_id": session_id, "limit": 200},
        )
        resp.raise_for_status()
        return resp.json()


def main() -> int:
    ok = True
    with httpx.Client(timeout=600) as client:
        # --- 1. 全局审批配置 ---
        r = client.get(f"{BASE}/security/settings")
        print("[1] 当前安全配置:", r.json())
        r = client.put(
            f"{BASE}/security/settings",
            json={"approval": {"enabled": True, "tools": ["delete", "write_file"]}},
        )
        print("    PUT 后:", r.json())
        assert r.json()["approval"]["tools"] == ["delete", "write_file"]

        # --- 2. 新建 agent（无 agent 级审批规则） ---
        r = client.post(f"{BASE}/agents", json={"agent_id": AGENT})
        print("[2] 新建 agent:", r.status_code)

        # --- 3. 全局规则应触发审批（所有 agent 遵守） ---
        sid = "sec-global-hitl"
        ev_types: list[str] = []
        interrupted = False
        with client.stream(
            "POST",
            f"{BASE}/chat/stream",
            json={"agent_id": AGENT, "message": "请删除文件 /sec_test.txt（若不存在请先创建再删除）",
                  "session_id": sid},
        ) as resp:
            current = ""
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("event: "):
                    current = line[7:].strip()
                    ev_types.append(current)
                elif line.startswith("data: ") and current == "interrupts":
                    data = json.loads(line[6:])
                    if data.get("approval_request"):
                        interrupted = True
                        print("[3] 全局审批已触发:",
                              [a["name"] for a in data["approval_request"]["actions"]])
        print("    SSE:", ev_types[-6:])
        if interrupted:
            rounds = 0
            while True:
                rounds += 1
                resp = client.post(
                    f"{BASE}/chat/{AGENT}/sessions/{sid}/approval",
                    json={"decision": "approve"},
                )
                body = resp.json()
                if body.get("status") != "pending_approval":
                    print("    审批闭环完成（", rounds, "轮）:",
                          (body.get("content") or "")[:40])
                    break
                if rounds > 5:
                    ok = False
                    break
        else:
            ok = False
            print("    [FAIL] 全局配置未触发审批")

        # --- 4. 停止对话：SSE 中途断开 ---
        sid2 = "sec-global-stop"
        chunks_read = 0
        with client.stream(
            "POST",
            f"{BASE}/chat/stream",
            json={"agent_id": AGENT,
                  "message": "请写一篇 500 字的关于人工智能的报告，不要使用任何工具",
                  "session_id": sid2},
        ) as resp:
            current = ""
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("event: "):
                    current = line[7:].strip()
                elif line.startswith("data: ") and current == "messages":
                    chunks_read += 1
                    if chunks_read >= 3:
                        print("[4] 已读", chunks_read, "个消息块，断开连接（停止对话）")
                        break
        print("    连接已关闭")

        # 后端应停止执行并保留部分回复
        time.sleep(2)
        msgs = history(sid2)
        roles = [m["role"] for m in msgs]
        print("    停止后历史:", roles, "条数:", len(msgs))
        partial = [m for m in msgs if m.get("role") == "assistant"]
        if partial and partial[-1].get("content"):
            print("    OK: 部分回复已保留（", len(partial[-1]["content"]), "字符）")
        else:
            ok = False
            print("    [FAIL] 停止后部分回复未持久化")

        # 后端未卡死：再发一条消息应正常完成
        r = client.post(
            f"{BASE}/chat/stream",
            json={"agent_id": AGENT, "message": "回复两个字：明白", "session_id": sid2},
        )
        print("[5] 停止后再次对话:", r.status_code,
              "（stream 端点应 200）")

        # --- 6. 清理：恢复默认 ---
        client.put(
            f"{BASE}/security/settings",
            json={"approval": {"enabled": False, "tools": []}},
        )
        print("[6] 已恢复默认安全配置")

    print("\n验证完成:", "ALL OK" if ok else "HAS ISSUES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
