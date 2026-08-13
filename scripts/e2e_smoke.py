"""AgentCore 端到端冒烟测试

针对已启动的后端执行只读验证：健康检查、Default Agent、bootstrap.md、
workspace 目录结构，以及 tools / envs / stats / models / skills / mcp /
files 等核心 API。

运行方式（后端需已启动）::

    python scripts/e2e_smoke.py

可通过环境变量 AGENTCORE_E2E_BASE 覆盖默认地址（http://127.0.0.1:8000）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

BASE = os.environ.get("AGENTCORE_E2E_BASE", "http://127.0.0.1:8000")
REPO_ROOT = Path(__file__).resolve().parents[1]

# 共享 HTTP 客户端（带超时，避免单个测试卡死）
_client = httpx.Client(base_url=BASE, timeout=30)


def _get(path: str) -> httpx.Response:
    return _client.get(path)


def _data_dir() -> Path:
    """解析数据目录（与后端 paths.py 相同的规则）"""
    data_dir = Path(os.environ.get("AGENTCORE_DATA_DIR", ".agentcore"))
    if not data_dir.is_absolute():
        data_dir = REPO_ROOT / data_dir
    return data_dir


def test_health():
    """健康检查"""
    r = _get("/health")
    assert r.status_code == 200, f"GET /health -> {r.status_code}"


def test_default_agent():
    """Default Agent 存在"""
    r = _get("/api/agents")
    assert r.status_code == 200, f"GET /api/agents -> {r.status_code}"
    agents = r.json()
    assert any(a["agent_id"] == "default" for a in agents), (
        f"Default Agent 不存在，现有 agents: "
        f"{[a.get('agent_id') for a in agents]}"
    )


def test_bootstrap_exists():
    """bootstrap.md 存在"""
    r = _get("/api/agents/default/files/content?path=bootstrap.md")
    assert r.status_code == 200, (
        f"GET bootstrap.md -> {r.status_code}: {r.text[:200]}"
    )


def test_workspace_structure():
    """目录结构验证（与后端 paths.py 相同的解析规则）"""
    workspace = _data_dir() / "workspace" / "agent" / "default"
    assert workspace.exists(), f"workspace 目录不存在: {workspace}"
    assert (workspace / "agent.md").exists(), "缺少 agent.md"
    assert (workspace / "soul.md").exists(), "缺少 soul.md"
    assert (workspace / "agent.json").exists(), "缺少 agent.json"


def test_tools_api():
    """工具 API"""
    r = _get("/api/agents/default/tools")
    assert r.status_code == 200, f"GET tools -> {r.status_code}"
    tools = r.json()["tools"]
    assert len(tools) > 0, "工具列表为空"


def test_envs_api():
    """环境变量 API"""
    r = _get("/api/envs")
    assert r.status_code == 200, f"GET /api/envs -> {r.status_code}"


def test_stats_api():
    """统计 API"""
    r = _get("/api/stats")
    assert r.status_code == 200, f"GET /api/stats -> {r.status_code}"


def test_models_api():
    """模型 API"""
    r = _get("/api/models")
    assert r.status_code == 200, f"GET /api/models -> {r.status_code}"


def test_skills_pool():
    """技能池"""
    r = _get("/api/skills/pool")
    assert r.status_code == 200, f"GET /api/skills/pool -> {r.status_code}"


def test_mcp_config():
    """MCP 配置"""
    r = _get("/api/agents/default/mcp")
    assert r.status_code == 200, f"GET mcp -> {r.status_code}"


def test_files_api():
    """文件 API"""
    r = _get("/api/agents/default/files/tree?path=.")
    assert r.status_code == 200, f"GET files/tree -> {r.status_code}"


def test_put_model_api():
    """PUT /api/agents/{id}/model：幂等写回当前模型配置 + 未知 agent 404"""
    payload = {
        "provider": "openai",
        "name": "qwen3.6-plus",
        "base_url": "https://coding.dashscope.aliyuncs.com/v1",
        "api_key_env": "AGENTCORE_LLM_API_KEY",
    }
    r = _client.put("/api/agents/default/model", json=payload)
    assert r.status_code == 200, (
        f"PUT /api/agents/default/model -> {r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    assert body.get("result") == "ok", f"PUT model result != ok: {body}"
    # 写入后 agent.json 应保持目标配置（未被意外切换）
    agent_json = _data_dir() / "workspace" / "agent" / "default" / "agent.json"
    cfg = json.loads(agent_json.read_text(encoding="utf-8"))
    assert cfg["model"]["name"] == "qwen3.6-plus", (
        f"default 模型被意外修改: {cfg['model']}"
    )
    # 未知 agent -> 404
    r404 = _client.put("/api/agents/__not_exist__/model", json=payload)
    assert r404.status_code == 404, (
        f"PUT model(未知 agent) -> {r404.status_code}，期望 404"
    )


def test_default_subagents_enabled():
    """default agent 的 settings.enable_subagents == true"""
    agent_json = _data_dir() / "workspace" / "agent" / "default" / "agent.json"
    assert agent_json.exists(), f"缺少 agent.json: {agent_json}"
    cfg = json.loads(agent_json.read_text(encoding="utf-8"))
    assert cfg.get("settings", {}).get("enable_subagents") is True, (
        f"enable_subagents 未开启: {cfg.get('settings')}"
    )


def test_stop_unload():
    """/stop 卸载后，文件 API 仍能懒加载返回 200"""
    r = _client.post("/api/agents/default/stop")
    assert r.status_code == 200, (
        f"POST /api/agents/default/stop -> {r.status_code}: {r.text[:200]}"
    )
    r2 = _get("/api/agents/default/files/tree?path=.")
    assert r2.status_code == 200, (
        f"stop 后 GET files/tree -> {r2.status_code}: {r2.text[:200]}"
    )


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    tests = [
        test_health,
        test_default_agent,
        test_bootstrap_exists,
        test_workspace_structure,
        test_tools_api,
        test_envs_api,
        test_stats_api,
        test_models_api,
        test_skills_pool,
        test_mcp_config,
        test_files_api,
        test_put_model_api,
        test_default_subagents_enabled,
        test_stop_unload,
    ]

    # 前置检查：后端未启动时给出清晰提示，而不是逐条报错。
    try:
        test_health()
    except httpx.ConnectError:
        print(f"❌ 无法连接后端 {BASE}")
        print("   后端似乎未启动，请先运行: .\\scripts\\start-local.ps1")
        print("   或手动启动: python -m uvicorn agentcore.api:app --workers 1")
        sys.exit(2)
    except Exception as e:
        print(f"❌ 健康检查失败: {e}")
        sys.exit(2)

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"✅ {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__}: {e}")
            failed += 1

    print(f"\n{'=' * 40}")
    print(f"通过: {passed}, 失败: {failed}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
