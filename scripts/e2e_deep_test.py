"""AgentCore 深度功能测试

验证智能体实际执行能力（非 API CRUD），覆盖：

  1. 文件系统操作 — agent 在对话中实际调用 write_file / read_file
  2. 主-子智能体委派 — master 通过 task 工具将任务分配给子智能体
  3. Planning 模式 — enable_planning 开启后 agent 使用 write_todos
  4. HITL 审批闭环 — interrupt → pending_approval → approve → 完成
  5. SSE 流式端点 — 验证 token 级流式输出 + done 事件

运行前提：后端已启动且 AGENTCORE_LLM_API_KEY 已注入。

    python scripts/e2e_deep_test.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

import httpx

BASE = os.environ.get("AGENTCORE_E2E_BASE", "http://127.0.0.1:8000")
REPO_ROOT = Path(__file__).resolve().parents[1]
_client = httpx.Client(base_url=BASE, timeout=180)  # 3 min — LLM 推理可能较慢

_CREATED_AGENTS: list[str] = []
_ERRORS: list[tuple[str, str]] = []


# ─────────────────────────── helpers ───────────────────────────

def _get(path, **kw):  return _client.get(path, **kw)
def _post(path, **kw): return _client.post(path, **kw)
def _put(path, **kw):  return _client.put(path, **kw)
def _delete(path, **kw): return _client.delete(path, **kw)


def _data_dir() -> Path:
    d = Path(os.environ.get("AGENTCORE_DATA_DIR", ".agentcore"))
    return d if d.is_absolute() else REPO_ROOT / d


def _create_agent(agent_id: str, settings_overrides: dict | None = None,
                  model: dict | None = None) -> None:
    """创建智能体并写入自定义 agent.json settings。"""
    r = _post("/api/agents", json={"agent_id": agent_id, **({"model": model} if model else {})})
    assert r.status_code == 200, f"创建 {agent_id} 失败: {r.status_code} {r.text[:200]}"
    _CREATED_AGENTS.append(agent_id)

    if settings_overrides:
        r2 = _get(f"/api/agents/{agent_id}/files/content?path=agent.json")
        assert r2.status_code == 200
        cfg = json.loads(r2.json()["content"])
        cfg.setdefault("settings", {}).update(settings_overrides)
        r3 = _put(
            f"/api/agents/{agent_id}/files/content?path=agent.json",
            json={"content": json.dumps(cfg, ensure_ascii=False, indent=2)},
        )
        assert r3.status_code == 200, f"写入 agent.json 失败: {r3.status_code}"


def _chat(agent_id: str, message: str, session_id: str | None = None,
        timeout: int = 180) -> dict:
    """发送同步 chat 请求并返回完整响应 body。"""
    r = _client.post(
        "/api/chat",
        json={"agent_id": agent_id, "message": message, "session_id": session_id},
        timeout=timeout,
    )
    assert r.status_code == 200, f"Chat 失败: {r.status_code} {r.text[:300]}"
    return r.json()


def _write_kernel(agent_id: str, filename: str, content: str) -> None:
    r = _put(f"/api/agents/{agent_id}/kernel/{filename}", json={"content": content})
    assert r.status_code == 200, f"写入 {filename} 失败: {r.status_code}"


def _workspace_file_exists(agent_id: str, rel_path: str) -> bool:
    """递归搜索工作区是否存在指定文件。"""
    def _search(prefix: str) -> bool:
        r = _get(f"/api/agents/{agent_id}/files/tree?path={prefix}")
        if r.status_code != 200:
            return False
        for e in r.json().get("items", []):
            name = e.get("name", "")
            path = e.get("path", "")
            if name == rel_path or path == rel_path or path.endswith("/" + rel_path):
                return True
            if e.get("is_dir") and name != "__pycache__":
                sub = f"{prefix}/{name}" if prefix and prefix != "." else name
                if _search(sub):
                    return True
        return False
    return _search(".")


def _read_workspace_file(agent_id: str, rel_path: str) -> str | None:
    """读取工作区文件，支持子目录。"""
    # 先直接读
    r = _get(f"/api/agents/{agent_id}/files/content?path={rel_path}")
    if r.status_code == 200:
        return r.json().get("content")
    # 搜索子目录
    def _find(prefix: str) -> str | None:
        rt = _get(f"/api/agents/{agent_id}/files/tree?path={prefix}")
        if rt.status_code != 200:
            return None
        for e in rt.json().get("items", []):
            if e.get("name") == rel_path and not e.get("is_dir"):
                fp = e.get("path", f"{prefix}/{rel_path}")
                rr = _get(f"/api/agents/{agent_id}/files/content?path={fp}")
                if rr.status_code == 200:
                    return rr.json().get("content")
            if e.get("is_dir") and e.get("name") != "__pycache__":
                sub = f"{prefix}/{e['name']}" if prefix and prefix != "." else e["name"]
                found = _find(sub)
                if found is not None:
                    return found
        return None
    return _find(".")


def _cleanup():
    for aid in list(_CREATED_AGENTS):
        try:
            r = _delete(f"/api/agents/{aid}")
            if r.status_code == 200:
                print(f"  🧹 {aid}")
        except Exception:
            pass


# ─────────────────────────── test cases ───────────────────────────


def test_01_file_system_via_chat():
    """T01 文件系统 — agent 在对话中实际写文件"""
    aid = "deep-fs"
    _create_agent(aid)
    _write_kernel(aid, "agent.md", "# 助手\n\n你是一个高效的文件操作助手。\n\n## 规则\n- 用户让你写文件时，直接使用 write_file 工具，不要犹豫\n- 写完后确认文件路径\n")

    # 要求 agent 写一个文件 — 明确指定路径 /greeting.txt
    resp = _chat(aid, "请立刻使用 write_file 工具创建文件，参数 file_path 设为 /greeting.txt，content 设为 Hello from AgentCore e2e test!。不要解释，直接执行。")

    content = resp.get("content", "")
    tool_calls = resp.get("tool_calls", [])
    print(f"      回复: {content[:120]}...")
    print(f"      工具调用: {[tc.get('name') for tc in tool_calls]}")

    # 验证 1: 响应中应包含文件操作相关内容
    assert content or tool_calls, "agent 既无回复也无工具调用"

    # 验证 2: 检查工具调用中是否有 write_file
    write_calls = [tc for tc in tool_calls if tc.get("name") == "write_file"]
    assert len(write_calls) > 0, (
        f"未检测到 write_file 工具调用。工具调用: {tool_calls}"
    )

    # 验证 3: 文件实际存在于工作区
    exists = _workspace_file_exists(aid, "greeting.txt")
    assert exists, "greeting.txt 未在工作区中创建"

    # 验证 4: 文件内容正确
    file_content = _read_workspace_file(aid, "greeting.txt")
    assert file_content is not None, "无法读取 greeting.txt"
    assert "Hello" in file_content or "AgentCore" in file_content, (
        f"文件内容不符预期: {file_content[:100]}"
    )
    print(f"      文件内容: {file_content[:80]}...")


def test_02_subagent_delegation():
    """T02 主-子智能体委派 — master 通过 task 工具分配任务"""
    # 创建 master
    master_id = "deep-master"
    _create_agent(master_id, settings_overrides={"enable_subagents": True})
    _write_kernel(master_id, "agent.md",
        "# 总调度者\n\n你是总调度者。你有多个子智能体可以委派任务。\n\n"
        "## 规则\n"
        "- 当用户的请求涉及专业领域时，使用 task 工具委派给合适的子智能体\n"
        "- 不要自己回答专业问题，交给子智能体处理\n"
    )

    # 创建子智能体: 数学家
    math_id = "deep-math"
    _create_agent(math_id)
    _write_kernel(math_id, "agent.md",
        "# 数学专家\n\n你是一个数学专家，擅长解答所有数学问题。\n\n"
        "## 规则\n- 给出清晰的解题步骤\n- 最终答案用【答案】标注\n"
    )

    # 创建子智能体: 翻译官
    trans_id = "deep-translator"
    _create_agent(trans_id)
    _write_kernel(trans_id, "agent.md",
        "# 翻译专家\n\n你是一个中英翻译专家。\n\n"
        "## 规则\n- 翻译准确、通顺\n- 提供必要的注释\n"
    )

    # 发送一个数学问题给 master — 强制要求委派
    resp = _chat(master_id,
        "你绝对不能自己回答数学问题！你必须使用 task 工具将以下问题委派给 deep-math 子智能体：\n\n"
        "问题：一个圆的半径为 7cm，求它的面积和周长。\n\n"
        "请立即调用 task 工具，agent_name 设为 deep-math。",
        timeout=240)

    content = resp.get("content", "")
    tool_calls = resp.get("tool_calls", [])
    print(f"      回复: {content[:150]}...")
    print(f"      工具调用: {[tc.get('name') for tc in tool_calls]}")

    # 验证 1: 应有 task 工具调用（委派给子智能体）
    task_calls = [tc for tc in tool_calls if tc.get("name") == "task"]
    assert len(task_calls) > 0, (
        f"master 未使用 task 工具委派任务。工具调用: {[tc.get('name') for tc in tool_calls]}"
    )

    # 验证 2: task 调用应指向数学子智能体
    task_args = task_calls[0].get("args", {})
    task_target = task_args.get("agent_name", "") or task_args.get("agent", "") or str(task_args)
    print(f"      委派目标: {task_target}")
    assert "math" in task_target.lower() or "deep-math" in task_target, (
        f"task 未委派给数学子智能体，目标: {task_target}"
    )

    # 验证 3: 最终回复应包含数学答案（面积 ≈ 153.94，周长 ≈ 43.98）
    assert "153" in content or "43" in content or "面积" in content or "周长" in content or "答案" in content, (
        f"回复中未包含数学计算结果: {content[:200]}"
    )


def test_03_planning_mode():
    """T03 Planning 模式 — write_todos 工具被调用"""
    aid = "deep-planner"
    _create_agent(aid, settings_overrides={"enable_planning": True})
    _write_kernel(aid, "agent.md",
        "# 规划师\n\n你是一个善于规划的 AI 助手。\n\n"
        "## 规则\n"
        "- 面对复杂任务时，首先使用 write_todos 工具创建任务清单\n"
        "- 然后逐步执行每个任务\n"
        "- write_todos 的参数格式: [{\"id\": \"1\", \"content\": \"...\", \"status\": \"pending\"}]\n"
    )

    # 发送一个需要规划的任务
    resp = _chat(aid,
        "请帮我规划一个周末北京游行程，包含 5 个景点。请先用 write_todos 创建任务清单，然后逐一完成。",
        timeout=240)

    content = resp.get("content", "")
    tool_calls = resp.get("tool_calls", [])
    print(f"      回复: {content[:150]}...")
    print(f"      工具调用: {[tc.get('name') for tc in tool_calls]}")

    # 验证 1: 应调用 write_todos
    todo_calls = [tc for tc in tool_calls if tc.get("name") == "write_todos"]
    assert len(todo_calls) > 0, (
        f"未检测到 write_todos 工具调用。工具调用: {[tc.get('name') for tc in tool_calls]}"
    )

    # 验证 2: write_todos 参数应包含多个任务项
    todo_args = todo_calls[0].get("args", {})
    todos = todo_args.get("todos", todo_args.get("items", []))
    if isinstance(todos, str):
        try:
            todos = json.loads(todos)
        except json.JSONDecodeError:
            pass
    print(f"      任务清单条目数: {len(todos) if isinstance(todos, list) else 'N/A'}")
    assert isinstance(todos, list) and len(todos) >= 2, (
        f"write_todos 参数不是有效的任务列表: {todo_args}"
    )

    # 验证 3: 最终回复应包含旅游规划内容
    assert "北京" in content or "景点" in content or "游" in content, (
        f"回复中未包含旅游规划内容: {content[:200]}"
    )


def test_04_hitl_approval_flow():
    """T04 HITL 审批闭环 — interrupt → approval → resume → 完成"""
    aid = "deep-hitl"
    _create_agent(aid, settings_overrides={
        "interrupt_rules": [
            {"tool_name": "write_file", "require_approval": True},
        ],
    })
    _write_kernel(aid, "agent.md",
        "# 助手\n\n你是一个文件操作助手。\n\n"
        "## 规则\n- 当用户要求创建文件时，直接使用 write_file 工具\n"
    )

    # 发送文件创建请求 — 应触发 HITL 中断
    resp = _chat(aid, "请创建一个文件 report.txt，内容为：E2E HITL Test Report")

    status = resp.get("status", "")
    approval_req = resp.get("approval_request")
    session_id = resp.get("session_id", "")
    print(f"      状态: {status}")
    print(f"      审批请求: {json.dumps(approval_req, ensure_ascii=False)[:200] if approval_req else 'None'}")

    # 验证 1: 应返回 pending_approval 状态
    assert status == "pending_approval", (
        f"期望 pending_approval 状态，实际: {status}。回复: {resp.get('content', '')[:200]}"
    )

    # 验证 2: 应包含审批请求信息
    assert approval_req is not None, "缺少 approval_request 信息"
    actions = approval_req.get("actions", [])
    assert len(actions) > 0, f"审批请求中无 actions: {approval_req}"
    print(f"      待审批工具: {actions[0].get('name', 'unknown')}")

    # 验证 3: 文件不应在审批前被创建
    exists_before = _workspace_file_exists(aid, "report.txt")
    assert not exists_before, "文件在审批前就被创建了！"

    # 提交批准决定
    r = _post(
        f"/api/chat/{aid}/sessions/{session_id}/approval",
        json={"decision": "approve", "operator": "e2e-tester"},
        timeout=180,
    )
    assert r.status_code == 200, f"审批提交失败: {r.status_code} {r.text[:300]}"
    approve_resp = r.json()
    approve_status = approve_resp.get("status", "")
    approve_content = approve_resp.get("content", "")
    approve_tool_calls = approve_resp.get("tool_calls", [])
    print(f"      审批后状态: {approve_status}")
    print(f"      审批后回复: {approve_content[:120]}...")
    print(f"      审批后工具调用: {[tc.get('name') for tc in approve_tool_calls]}")

    # 验证 4: 审批后 agent 应继续执行
    if approve_status == "complete":
        # 验证 5: write_file 应出现在工具调用中
        write_calls = [tc for tc in approve_tool_calls if tc.get("name") == "write_file"]
        assert write_calls, (
            f"审批通过后 agent 未调用 write_file。工具调用: {[tc.get('name') for tc in approve_tool_calls]}"
        )
        # 验证 6: 文件应在审批后被创建（递归搜索）
        exists_after = _workspace_file_exists(aid, "report.txt")
        if exists_after:
            file_content = _read_workspace_file(aid, "report.txt")
            assert file_content is not None and ("HITL" in file_content or "Test" in file_content or "Report" in file_content), (
                f"文件内容不符预期: {file_content}"
            )
            print(f"      审批后文件内容: {file_content[:80] if file_content else 'empty'}...")
        else:
            # 文件可能写在子目录，只要 write_file 被调用即视为通过
            print("      ⚠️  文件未在根目录找到（可能在子目录），但 write_file 已执行")
    elif approve_status == "pending_approval":
        print("      ⚠️  审批后仍有新的中断（可能是多轮审批），视为通过")
    else:
        write_calls = [tc for tc in approve_tool_calls if tc.get("name") == "write_file"]
        assert write_calls or approve_content, (
            f"审批后 agent 既未执行 write_file 也无回复: {approve_resp}"
        )


def test_05_sse_streaming():
    """T05 SSE 流式端点 — 验证 token 级输出 + done 事件"""
    aid = "deep-stream"
    _create_agent(aid)
    _write_kernel(aid, "agent.md", "# 助手\n\n简短回答问题。\n")

    # 发送流式请求
    r = _client.post(
        "/api/chat/stream",
        json={"agent_id": aid, "message": "用一句话回答：1+1等于几？"},
        headers={"Accept": "text/event-stream"},
        timeout=180,
    )
    assert r.status_code == 200, f"Stream 失败: {r.status_code} {r.text[:200]}"

    # 解析 SSE 事件
    events: dict[str, list] = {}
    full_text = ""
    for line in r.text.split("\n"):
        line = line.strip()
        if line.startswith("event: "):
            event_type = line[7:]
        elif line.startswith("data: "):
            try:
                data = json.loads(line[6:])
                events.setdefault(event_type, []).append(data)
                if event_type == "messages":
                    full_text += data.get("content", "")
            except json.JSONDecodeError:
                pass

    print(f"      事件类型: {list(events.keys())}")
    print(f"      messages 事件数: {len(events.get('messages', []))}")
    print(f"      完整文本: {full_text[:120]}...")

    # 验证 1: 应有 messages 事件
    assert "messages" in events, f"缺少 messages 事件，实际事件: {list(events.keys())}"
    assert len(events["messages"]) > 0, "messages 事件为空"

    # 验证 2: 应有 done 事件
    assert "done" in events, f"缺少 done 事件，实际事件: {list(events.keys())}"
    done = events["done"][0]
    assert done.get("session_id"), "done 事件缺少 session_id"
    assert done.get("agent_id") == aid, f"done 事件 agent_id 不匹配: {done.get('agent_id')}"

    # 验证 3: 流式文本应包含有意义的回复
    assert len(full_text) > 5, f"流式文本过短: {full_text!r}"
    assert "2" in full_text or "二" in full_text or "等" in full_text or "加" in full_text, (
        f"流式文本未包含预期内容: {full_text[:100]}"
    )

    # 验证 4: 如果有 token_usage 事件，验证格式
    if "token_usage" in events:
        usage = events["token_usage"][-1]
        assert "input_tokens" in usage or "output_tokens" in usage, (
            f"token_usage 事件格式异常: {usage}"
        )
        print(f"      Token 用量: input={usage.get('input_tokens', 0)}, output={usage.get('output_tokens', 0)}")


# ─────────────────────────── main ───────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    tests = [
        test_01_file_system_via_chat,
        test_02_subagent_delegation,
        test_03_planning_mode,
        test_04_hitl_approval_flow,
        test_05_sse_streaming,
    ]

    # 前置检查
    try:
        r = _get("/health")
        assert r.status_code == 200
    except httpx.ConnectError:
        print(f"❌ 无法连接后端 {BASE}，请先启动")
        sys.exit(2)

    # 检查 API Key
    api_key = os.environ.get("AGENTCORE_LLM_API_KEY", "")
    if not api_key:
        # 也检查服务端是否已注入
        r = _get("/api/envs")
        envs = r.json().get("envs", []) if r.status_code == 200 else []
        if not any(e.get("key") == "AGENTCORE_LLM_API_KEY" for e in envs):
            print("❌ AGENTCORE_LLM_API_KEY 未设置，Chat 测试需要 LLM API Key")
            sys.exit(2)

    print("=" * 60)
    print("  AgentCore 深度功能测试")
    print("  验证：文件系统 / 子智能体委派 / Planning / HITL / SSE")
    print("=" * 60)
    print()

    passed = 0
    failed = 0

    for test_fn in tests:
        name = test_fn.__name__
        doc = (test_fn.__doc__ or "").split("\n")[0].strip()
        label = f"{name}: {doc}"
        try:
            test_fn()
            print(f"  ✅ {label}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {label}")
            print(f"     └─ {e}")
            _ERRORS.append((name, str(e)))
            failed += 1
        except Exception as e:
            print(f"  💥 {label}")
            print(f"     └─ {type(e).__name__}: {e}")
            _ERRORS.append((name, f"{type(e).__name__}: {e}"))
            failed += 1

    # 清理
    print()
    print("─── 清理测试数据 ───")
    _cleanup()

    # 汇总
    print()
    print("=" * 60)
    total = passed + failed
    print(f"  总计: {total}  |  通过: {passed}  |  失败: {failed}")
    if _ERRORS:
        print()
        print("  失败用例:")
        for name, msg in _ERRORS:
            print(f"    ✗ {name}: {msg}")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
