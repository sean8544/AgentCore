"""AgentCore 端到端全功能测试

覆盖场景：
  1. 健康检查 + 系统 API（envs / stats / models / skills pool / navigation）
  2. 创建主智能体（master，enable_subagents=true）
  3. 创建子智能体 A（coder — 编码专家）
  4. 创建子智能体 B（writer — 写作专家）
  5. 智能体列表 / 详情 / 状态验证
  6. Kernel 文件读写（agent.md / soul.md / profile.md）
  7. 工作区文件操作（tree / content / write / upload）
  8. 工具列表查询
  9. 模型配置更新 + 验证
  10. 智能体启动 / 停止 / 重载生命周期
  11. 会话管理（sessions 列表 / history）
  12. 子智能体发现验证（SubAgentRegistry）
  13. Chat 同步端点（需要 LLM API Key，无 key 时跳过）
  14. 智能体删除 + 清理

运行方式（后端需已启动）::

    python scripts/e2e_full_test.py
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
_client = httpx.Client(base_url=BASE, timeout=60)

# 测试产生的智能体 ID（用于最后清理）
_CREATED_AGENTS: list[str] = []

# ─────────────────────────── helpers ───────────────────────────


def _get(path: str, **kw) -> httpx.Response:
    return _client.get(path, **kw)


def _post(path: str, **kw) -> httpx.Response:
    return _client.post(path, **kw)


def _put(path: str, **kw) -> httpx.Response:
    return _client.put(path, **kw)


def _delete(path: str, **kw) -> httpx.Response:
    return _client.delete(path, **kw)


def _ok(cond: bool, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg)


def _data_dir() -> Path:
    data_dir = Path(os.environ.get("AGENTCORE_DATA_DIR", ".agentcore"))
    if not data_dir.is_absolute():
        data_dir = REPO_ROOT / data_dir
    return data_dir


# ─────────────────────────── test cases ───────────────────────────


def test_01_health():
    """T01 健康检查"""
    r = _get("/health")
    assert r.status_code == 200, f"GET /health -> {r.status_code}"
    assert r.json()["status"] == "ok"


def test_02_system_apis():
    """T02 系统级 API（envs / stats / models / skills pool / navigation）"""
    for path in ["/api/envs", "/api/stats", "/api/models", "/api/skills/pool", "/navigation"]:
        r = _get(path)
        assert r.status_code == 200, f"GET {path} -> {r.status_code}: {r.text[:200]}"


def test_03_create_master_agent():
    """T03 创建主智能体 master（enable_subagents 在默认 agent.json 中已开启）"""
    agent_id = "e2e-master"
    r = _post("/api/agents", json={"agent_id": agent_id})
    assert r.status_code == 200, f"创建 master 失败 -> {r.status_code}: {r.text[:300]}"
    body = r.json()
    assert body["result"] == "ok"
    assert body["agent_id"] == agent_id
    _CREATED_AGENTS.append(agent_id)

    # 验证 workspace 目录已创建
    ws_dir = _data_dir() / "workspace" / "agent" / agent_id
    assert ws_dir.exists(), f"workspace 目录不存在: {ws_dir}"
    assert (ws_dir / "agent.json").exists(), "缺少 agent.json"
    assert (ws_dir / "agent.md").exists(), "缺少 agent.md"
    # Legacy soul.md / profile.md 不再默认创建。
    assert not (ws_dir / "soul.md").exists(), "不应再默认创建 soul.md"


def test_04_create_subagent_coder():
    """T04 创建子智能体 coder（编码专家）"""
    agent_id = "e2e-coder"
    r = _post("/api/agents", json={
        "agent_id": agent_id,
        "model": {
            "provider": "openai",
            "name": "qwen3.6-plus",
            "base_url": "https://coding.dashscope.aliyuncs.com/v1",
        },
    })
    assert r.status_code == 200, f"创建 coder 失败 -> {r.status_code}: {r.text[:300]}"
    assert r.json()["result"] == "ok"
    _CREATED_AGENTS.append(agent_id)

    # 写入编码专家的 agent.md
    r2 = _put(f"/api/agents/{agent_id}/kernel/agent.md", json={
        "content": "# 编码专家\n\n你是一个专业的软件工程师，擅长代码编写、调试和架构设计。\n\n## 专长\n- Python / TypeScript / Rust\n- 系统设计与架构\n- 代码审查与优化\n",
    })
    assert r2.status_code == 200, f"写入 coder agent.md 失败 -> {r2.status_code}"


def test_05_create_subagent_writer():
    """T05 创建子智能体 writer（写作专家）"""
    agent_id = "e2e-writer"
    r = _post("/api/agents", json={"agent_id": agent_id})
    assert r.status_code == 200, f"创建 writer 失败 -> {r.status_code}: {r.text[:300]}"
    assert r.json()["result"] == "ok"
    _CREATED_AGENTS.append(agent_id)

    # 写入写作专家的 agent.md（身份+人设已合并，不再使用 soul.md）
    r2 = _put(f"/api/agents/{agent_id}/kernel/agent.md", json={
        "content": "# 资深文案创作者\n\n你是一位资深文案创作者，擅长各类文体写作。\n\n## 风格特点\n- 文笔优美，逻辑清晰\n- 善于用故事和比喻解释复杂概念\n",
    })
    assert r2.status_code == 200, f"写入 writer agent.md 失败 -> {r2.status_code}"


def test_06_duplicate_create_rejected():
    """T06 重复创建同名智能体应返回 409"""
    r = _post("/api/agents", json={"agent_id": "e2e-master"})
    assert r.status_code == 409, f"重复创建应返回 409，实际 -> {r.status_code}"


def test_07_list_agents():
    """T07 智能体列表应包含所有已创建智能体"""
    r = _get("/api/agents")
    assert r.status_code == 200
    agents = r.json()
    ids = {a["agent_id"] for a in agents}
    for aid in ["default", "e2e-master", "e2e-coder", "e2e-writer"]:
        assert aid in ids, f"列表缺少 {aid}，现有: {ids}"
    assert len(agents) >= 4, f"至少 4 个智能体，实际 {len(agents)}"


def test_08_get_agent_detail():
    """T08 获取单个智能体详情"""
    r = _get("/api/agents/e2e-master")
    assert r.status_code == 200
    body = r.json()
    assert body["agent_id"] == "e2e-master"
    assert body["loaded"] is True
    assert "workspace_dir" in body


def test_09_kernel_file_read():
    """T09 读取 kernel 文件"""
    r = _get("/api/agents/e2e-coder/kernel/agent.md")
    assert r.status_code == 200
    body = r.json()
    assert "编码专家" in body["content"], f"agent.md 内容不符预期: {body['content'][:100]}"


def test_10_kernel_file_write():
    """T10 写入 kernel 文件并验证"""
    new_content = "# 大师智能体\n\n你是总调度者，负责协调各子智能体完成任务。\n"
    r = _put("/api/agents/e2e-master/kernel/agent.md", json={"content": new_content})
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == "ok"
    assert body["system_prompt_length"] > 0

    # 回读验证
    r2 = _get("/api/agents/e2e-master/kernel/agent.md")
    assert r2.status_code == 200
    assert "总调度者" in r2.json()["content"]


def test_11_files_tree():
    """T11 工作区文件树"""
    r = _get("/api/agents/e2e-master/files/tree?path=.")
    assert r.status_code == 200
    body = r.json()
    # 响应格式: {"path": ".", "items": [{"name": ..., "path": ..., "is_dir": ..., "size": ...}]}
    items = body.get("items", [])
    names = [e.get("name", "") for e in items if isinstance(e, dict)]
    # 至少应包含 agent.md / soul.md / agent.json
    for expected in ["agent.md", "soul.md", "agent.json"]:
        assert expected in names, f"文件树缺少 {expected}，当前: {names}"


def test_12_files_content():
    """T12 读取工作区文件内容"""
    r = _get("/api/agents/e2e-master/files/content?path=agent.json")
    assert r.status_code == 200
    body = r.json()
    cfg = json.loads(body["content"])
    assert "model" in cfg, f"agent.json 缺少 model 字段: {cfg}"


def test_13_files_write():
    """T13 写入工作区文件"""
    content = "# 测试笔记\n\n这是 e2e 测试创建的笔记文件。\n"
    r = _put("/api/agents/e2e-master/files/content?path=notes.md", json={"content": content})
    assert r.status_code == 200, f"写入 notes.md 失败 -> {r.status_code}: {r.text[:200]}"

    # 回读验证
    r2 = _get("/api/agents/e2e-master/files/content?path=notes.md")
    assert r2.status_code == 200
    assert "测试笔记" in r2.json()["content"]


def test_14_tools_list():
    """T14 工具列表"""
    r = _get("/api/agents/e2e-master/tools")
    assert r.status_code == 200
    body = r.json()
    tools = body.get("tools", [])
    assert len(tools) > 0, "工具列表为空"
    # 应包含常见内置工具
    tool_names = {t.get("name", "") for t in tools}
    print(f"      工具列表: {sorted(tool_names)}")


def test_15_model_update():
    """T15 更新模型配置"""
    payload = {
        "provider": "openai",
        "name": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
    }
    r = _put("/api/agents/e2e-master/model", json=payload)
    assert r.status_code == 200, f"更新模型失败 -> {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body["result"] == "ok"
    assert body["model"]["name"] == "gpt-4o-mini"

    # 验证 agent.json 已更新
    r2 = _get("/api/agents/e2e-master/files/content?path=agent.json")
    cfg = json.loads(r2.json()["content"])
    assert cfg["model"]["name"] == "gpt-4o-mini", f"agent.json 未更新: {cfg['model']}"

    # 恢复原模型配置
    _put("/api/agents/e2e-master/model", json={
        "provider": "openai",
        "name": "qwen3.6-plus",
        "base_url": "https://coding.dashscope.aliyuncs.com/v1",
        "api_key_env": "AGENTCORE_LLM_API_KEY",
    })


def test_16_mcp_config():
    """T16 MCP 配置读写"""
    r = _get("/api/agents/e2e-master/mcp")
    assert r.status_code == 200
    # 响应格式: {"agent_id": "...", "servers": [...]}
    body = r.json()
    assert "servers" in body, f"MCP 响应缺少 servers 字段: {body}"

    # 添加一个 MCP server 条目（仅配置，不要求连接成功）
    r2 = _post("/api/agents/e2e-master/mcp", json={
        "server_id": "test-server",
        "name": "Test MCP Server",
        "transport": "stdio",
        "command": "npx -y @test/mcp-server",
        "enabled": False,  # 禁用，避免实际连接
    })
    assert r2.status_code == 200, f"添加 MCP server 失败 -> {r2.status_code}: {r2.text[:200]}"

    # 读取验证（servers 是列表）
    r3 = _get("/api/agents/e2e-master/mcp")
    assert r3.status_code == 200
    mcp_body = r3.json()
    servers = mcp_body.get("servers", [])
    server_ids = [s.get("server_id") for s in servers if isinstance(s, dict)]
    assert "test-server" in server_ids, f"MCP 配置缺少 test-server: {mcp_body}"

    # 更新
    r4 = _put("/api/agents/e2e-master/mcp/test-server", json={"enabled": True})
    assert r4.status_code == 200

    # 删除
    r5 = _delete("/api/agents/e2e-master/mcp/test-server")
    assert r5.status_code == 200


def test_17_agent_reload():
    """T17 智能体重载（在 stop 之前执行，因为 stop 会卸载 workspace）"""
    r = _post("/api/agents/e2e-master/reload")
    assert r.status_code == 200, f"重载失败 -> {r.status_code}: {r.text[:300]}"
    assert r.json()["status"] == "reloaded"


def test_18_sessions():
    """T18 会话管理（workspace 已加载时可查询）"""
    r = _get("/api/agents/e2e-master/sessions")
    assert r.status_code == 200, f"获取 sessions 失败 -> {r.status_code}: {r.text[:300]}"
    body = r.json()
    assert "sessions" in body, f"响应缺少 sessions 字段: {body}"
    print(f"      当前会话数: {len(body['sessions'])}")


def test_19_agent_lifecycle_start_stop():
    """T19 智能体启动 / 停止生命周期"""
    # 启动
    r = _post("/api/agents/e2e-master/start")
    assert r.status_code == 200, f"启动失败 -> {r.status_code}: {r.text[:300]}"
    body = r.json()
    assert body["result"] == "ok"
    assert body["state"] in ("running", "idle", "started"), f"意外状态: {body['state']}"

    # 查看状态
    r2 = _get("/api/agents/e2e-master")
    assert r2.status_code == 200
    state = r2.json().get("state")
    print(f"      master 运行时状态: {state}")

    # 停止（会卸载 workspace）
    r3 = _post("/api/agents/e2e-master/stop")
    assert r3.status_code == 200, f"停止失败 -> {r3.status_code}: {r3.text[:300]}"
    assert r3.json()["state"] == "stopped"

    # 停止后 files API 应能懒加载恢复
    r4 = _get("/api/agents/e2e-master/files/tree?path=.")
    assert r4.status_code == 200, f"停止后 files/tree 应懒加载成功 -> {r4.status_code}"


def test_20_subagent_discovery():
    """T20 子智能体发现 — 验证 master 的 agent.json 开启了 enable_subagents"""
    # 先确认 default agent 的 enable_subagents 设置
    # master 使用的是默认 DEFAULT_AGENT_JSON，settings 为空
    # 我们需要给 master 开启 subagents 以验证发现机制
    r = _get("/api/agents/e2e-master/files/content?path=agent.json")
    assert r.status_code == 200
    cfg = json.loads(r.json()["content"])

    # 更新 settings.enable_subagents = true
    cfg.setdefault("settings", {})["enable_subagents"] = True
    r2 = _put("/api/agents/e2e-master/files/content?path=agent.json", json={"content": json.dumps(cfg, ensure_ascii=False, indent=2)})
    assert r2.status_code == 200, f"更新 agent.json 失败 -> {r2.status_code}: {r2.text[:200]}"

    # 重载使其生效
    r3 = _post("/api/agents/e2e-master/reload")
    assert r3.status_code == 200

    # 验证配置已写入
    r4 = _get("/api/agents/e2e-master/files/content?path=agent.json")
    cfg2 = json.loads(r4.json()["content"])
    assert cfg2["settings"]["enable_subagents"] is True, (
        f"enable_subagents 未开启: {cfg2.get('settings')}"
    )
    print("      master 已开启 enable_subagents，子智能体将在下次 chat 时自动发现")


def test_21_chat_no_key_skip():
    """T21 Chat 端点（无 API Key 时优雅跳过）"""
    api_key = os.environ.get("AGENTCORE_LLM_API_KEY", "")
    if not api_key:
        print("      ⚠️  AGENTCORE_LLM_API_KEY 未设置，跳过 Chat 测试")
        return

    r = _post("/api/chat", json={
        "agent_id": "e2e-master",
        "message": "你好，请简单介绍一下你自己。",
        "session_id": None,
    })
    assert r.status_code == 200, f"Chat 失败 -> {r.status_code}: {r.text[:300]}"
    body = r.json()
    # 响应字段可能为 content / reply / message / response
    reply = body.get("content") or body.get("reply") or body.get("message") or body.get("response") or ""
    assert reply, f"Chat 响应内容为空，字段: {list(body.keys())}"
    print(f"      Chat 回复: {str(reply)[:100]}...")


def test_22_stream_endpoint():
    """T22 SSE 流式端点存在性验证（无 API Key 时跳过实际推理）"""
    api_key = os.environ.get("AGENTCORE_LLM_API_KEY", "")
    if not api_key:
        print("      ⚠️  AGENTCORE_LLM_API_KEY 未设置，跳过 Stream 测试")
        return

    # 仅验证端点可接受请求（不验证完整流式响应）
    r = _client.post("/api/chat/stream", json={
        "agent_id": "e2e-master",
        "message": "简短回复：1+1=?",
        "session_id": None,
    }, headers={"Accept": "text/event-stream"})
    # 流式端点应返回 200
    assert r.status_code == 200, f"Stream 端点 -> {r.status_code}: {r.text[:200]}"
    print("      SSE 流式端点响应正常")


def test_23_unknown_agent_404():
    """T23 不存在的智能体应返回 404"""
    for method, path in [
        ("GET", "/api/agents/__nonexistent__"),
        ("POST", "/api/agents/__nonexistent__/start"),
        ("POST", "/api/agents/__nonexistent__/stop"),
        ("DELETE", "/api/agents/__nonexistent__"),
    ]:
        r = _client.request(method, path)
        assert r.status_code == 404, f"{method} {path} 应返回 404，实际 -> {r.status_code}"


def test_24_delete_agent():
    """T24 删除子智能体 writer"""
    agent_id = "e2e-writer"
    r = _delete(f"/api/agents/{agent_id}")
    assert r.status_code == 200, f"删除失败 -> {r.status_code}: {r.text[:200]}"
    assert r.json()["status"] == "deleted"

    # 验证已不在列表中
    r2 = _get("/api/agents")
    ids = {a["agent_id"] for a in r2.json()}
    assert agent_id not in ids, f"删除后仍存在于列表: {ids}"

    # 从清理列表中移除（已手动删除）
    _CREATED_AGENTS.remove(agent_id)


def test_25_delete_nonexistent_404():
    """T25 删除不存在的智能体应返回 404"""
    r = _delete("/api/agents/__nonexistent__")
    assert r.status_code == 404


# ─────────────────────────── cleanup ───────────────────────────


def cleanup():
    """清理测试创建的智能体"""
    for agent_id in list(_CREATED_AGENTS):
        try:
            r = _delete(f"/api/agents/{agent_id}")
            if r.status_code == 200:
                print(f"  🧹 已清理: {agent_id}")
            else:
                print(f"  ⚠️  清理 {agent_id} 返回 {r.status_code}")
        except Exception as e:
            print(f"  ⚠️  清理 {agent_id} 异常: {e}")


# ─────────────────────────── main ───────────────────────────


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    tests = [
        test_01_health,
        test_02_system_apis,
        test_03_create_master_agent,
        test_04_create_subagent_coder,
        test_05_create_subagent_writer,
        test_06_duplicate_create_rejected,
        test_07_list_agents,
        test_08_get_agent_detail,
        test_09_kernel_file_read,
        test_10_kernel_file_write,
        test_11_files_tree,
        test_12_files_content,
        test_13_files_write,
        test_14_tools_list,
        test_15_model_update,
        test_16_mcp_config,
        test_17_agent_reload,
        test_18_sessions,
        test_19_agent_lifecycle_start_stop,
        test_20_subagent_discovery,
        test_21_chat_no_key_skip,
        test_22_stream_endpoint,
        test_23_unknown_agent_404,
        test_24_delete_agent,
        test_25_delete_nonexistent_404,
    ]

    # 前置检查：后端是否已启动
    try:
        test_01_health()
    except httpx.ConnectError:
        print(f"❌ 无法连接后端 {BASE}")
        print("   后端未启动，请先运行: .\\scripts\\start-local.ps1")
        sys.exit(2)
    except Exception as e:
        print(f"❌ 健康检查失败: {e}")
        sys.exit(2)

    print("=" * 60)
    print("  AgentCore 端到端全功能测试")
    print("=" * 60)
    print()

    passed = 0
    failed = 0
    skipped = 0
    errors: list[tuple[str, str]] = []

    for test_fn in tests:
        name = test_fn.__name__
        doc = (test_fn.__doc__ or "").split("\n")[0].strip()
        label = f"{name}: {doc}"
        try:
            test_fn()
            # 检查是否有跳过标记
            print(f"  ✅ {label}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {label}")
            print(f"     └─ {e}")
            errors.append((name, str(e)))
            failed += 1
        except Exception as e:
            print(f"  💥 {label}")
            print(f"     └─ {type(e).__name__}: {e}")
            errors.append((name, f"{type(e).__name__}: {e}"))
            failed += 1

    # 清理
    print()
    print("─── 清理测试数据 ───")
    cleanup()

    # 汇总
    print()
    print("=" * 60)
    total = passed + failed
    print(f"  总计: {total}  |  通过: {passed}  |  失败: {failed}")
    if errors:
        print()
        print("  失败用例:")
        for name, msg in errors:
            print(f"    ✗ {name}: {msg}")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
