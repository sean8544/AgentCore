from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcore.api import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client running inside an isolated (temporary) data directory.

    The lifespan recreates the store / manager / service bound to the
    current working directory, so all persistence lands in *tmp_path*.
    """
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_navigation_endpoint(client) -> None:
    response = client.get("/navigation")
    assert response.status_code == 200
    body = response.json()
    keys = [item["domain_key"] for item in body]
    assert "workspace" in keys
    assert "settings" in keys


def test_profile_endpoints_removed(client) -> None:
    assert client.get("/api/profiles").status_code == 404
    assert client.post("/profiles/draft", json={}).status_code == 404
    assert client.post("/approvals", json={}).status_code == 404


def test_create_agent_with_default_config(client) -> None:
    response = client.post("/api/agents", json={"agent_id": "demo"})
    assert response.status_code == 200
    assert response.json()["agent_id"] == "demo"

    # agent.json is seeded with the default model configuration.
    agent_json = Path(".agentcore/workspace/agent/demo/agent.json")
    assert agent_json.exists()
    config = json.loads(agent_json.read_text(encoding="utf-8"))
    assert config["model"]["name"] == "qwen3.6-plus"
    assert config["tools"] == {"enabled": [], "disabled": []}

    # Kernel files are still seeded (memory=[] injection sources).
    # Only bootstrap.md and agent.md — legacy profile.md / soul.md are
    # no longer created for new workspaces.
    for name in ("bootstrap.md", "agent.md"):
        assert (Path(".agentcore/workspace/agent/demo") / name).exists()

    # Sessions persist outside the agent-visible workspace.
    sessions_dir = Path(".agentcore/data/agents/demo/sessions")
    assert sessions_dir.exists()

    listed = client.get("/api/agents").json()
    assert any(item["agent_id"] == "demo" for item in listed)
    assert all("profile_id" not in item for item in listed)


def test_create_agent_with_custom_model(client) -> None:
    response = client.post(
        "/api/agents",
        json={
            "agent_id": "custom",
            "model": {"provider": "qwen", "name": "qwen-max"},
        },
    )
    assert response.status_code == 200

    config = json.loads(
        Path(".agentcore/workspace/agent/custom/agent.json").read_text(encoding="utf-8")
    )
    assert config["model"]["provider"] == "qwen"
    assert config["model"]["name"] == "qwen-max"
    # Non-overridden default keys survive the merge.
    assert config["model"]["base_url"] == "https://coding.dashscope.aliyuncs.com/v1"


def test_create_agent_duplicate_rejected(client) -> None:
    assert client.post("/api/agents", json={"agent_id": "dup"}).status_code == 200
    assert client.post("/api/agents", json={"agent_id": "dup"}).status_code == 409


def test_kernel_file_round_trip(client) -> None:
    client.post("/api/agents", json={"agent_id": "kernel-demo"})

    read_resp = client.get("/api/agents/kernel-demo/kernel/agent.md")
    assert read_resp.status_code == 200
    assert "Agent" in read_resp.json()["content"]

    write_resp = client.put(
        "/api/agents/kernel-demo/kernel/soul.md",
        json={"content": "# 核心人设\n\n我是小明。"},
    )
    assert write_resp.status_code == 200

    # Legacy kernel files (profile.md / soul.md) remain writable and
    # readable even though new workspaces no longer seed them.
    reread = client.get("/api/agents/kernel-demo/kernel/soul.md")
    assert "小明" in reread.json()["content"]


def test_delete_agent(client) -> None:
    client.post("/api/agents", json={"agent_id": "gone"})
    delete_resp = client.delete("/api/agents/gone")
    assert delete_resp.status_code == 200
    assert client.get("/api/agents/gone").status_code == 404


def test_default_agent_bootstrapped_on_fresh_install(client) -> None:
    """A fresh install auto-creates the Default Agent with bootstrap.md."""
    listed = client.get("/api/agents").json()
    assert any(item["agent_id"] == "default" for item in listed)

    ws = Path(".agentcore/workspace/agent/default")
    for name in ("bootstrap.md", "agent.md", "agent.json"):
        assert (ws / name).exists()
    assert (ws / "skills").is_dir()
    assert (ws / ".bootstrap_seeded").exists()

    # bootstrap.md is exposed via the kernel-file API and injected into
    # the composed system prompt (memory=[] source).
    read_resp = client.get("/api/agents/default/kernel/bootstrap.md")
    assert read_resp.status_code == 200
    assert "欢迎使用 AgentCore" in read_resp.json()["content"]

    workspace = client.app.state.agent_manager.get_workspace("default")
    assert "欢迎使用 AgentCore" in workspace.get_system_prompt()


def test_bootstrap_md_never_recreated_after_deletion(monkeypatch, tmp_path) -> None:
    """Once bootstrap.md is removed (setup finished), restarts keep it gone."""
    monkeypatch.chdir(tmp_path)
    ws = Path(".agentcore/workspace/agent/default")

    with TestClient(app):
        assert (ws / "bootstrap.md").exists()
        (ws / "bootstrap.md").unlink()

    # Simulate a restart — the seeded flag prevents regeneration.
    with TestClient(app):
        assert not (ws / "bootstrap.md").exists()
        listed = json.loads(
            (Path(".agentcore/data/agents.json")).read_text(encoding="utf-8")
        )
        assert "default" in listed["agent_states"]


def test_migrate_workspace_layout_idempotent(monkeypatch, tmp_path) -> None:
    """Migration moves the legacy layout once and is a no-op afterwards."""
    monkeypatch.chdir(tmp_path)
    from agentcore.runtime.multi_agent_manager import migrate_workspace_layout

    # Seed the legacy layout.
    legacy = Path(".agentcore/workspaces/legacy-agent")
    legacy.mkdir(parents=True)
    (legacy / "agent.md").write_text("# old", encoding="utf-8")
    (legacy / "sessions.json").write_text('{"sessions": {}}', encoding="utf-8")

    migrate_workspace_layout()

    new_ws = Path(".agentcore/workspace/agent/legacy-agent")
    assert (new_ws / "agent.md").exists()
    assert not (new_ws / "sessions.json").exists()
    assert (
        Path(".agentcore/data/agents/legacy-agent/sessions/sessions.json").exists()
    )
    assert not Path(".agentcore/workspaces").exists()

    # Second run is a safe no-op.
    migrate_workspace_layout()
    assert (new_ws / "agent.md").exists()


# ---------------------------------------------------------------------------
# Stage-3 endpoints: tools / envs / token usage / stats / models
# ---------------------------------------------------------------------------


def test_tools_list_and_toggle(client) -> None:
    client.post("/api/agents", json={"agent_id": "tools-demo"})

    listed = client.get("/api/agents/tools-demo/tools")
    assert listed.status_code == 200
    tools = listed.json()["tools"]
    names = [t["name"] for t in tools]
    assert "read_file" in names and "execute" in names
    assert all(t["enabled"] for t in tools)

    # Disable a tool — persisted into agent.json.
    toggle = client.put(
        "/api/agents/tools-demo/tools/execute", json={"enabled": False}
    )
    assert toggle.status_code == 200
    assert toggle.json() == {"result": "ok", "tool": "execute", "enabled": False}

    config = json.loads(
        Path(".agentcore/workspace/agent/tools-demo/agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["tools"]["disabled"] == ["execute"]

    after = client.get("/api/agents/tools-demo/tools").json()
    states = {t["name"]: t["enabled"] for t in after["tools"]}
    assert states["execute"] is False and states["read_file"] is True

    # Re-enable.
    client.put("/api/agents/tools-demo/tools/execute", json={"enabled": True})
    assert all(t["enabled"] for t in client.get("/api/agents/tools-demo/tools").json()["tools"])

    assert client.get("/api/agents/ghost/tools").status_code == 404
    assert (
        client.put("/api/agents/tools-demo/tools/nope", json={"enabled": False}).status_code
        == 404
    )


def test_envs_crud_with_masking(client) -> None:
    listed = client.get("/api/envs")
    assert listed.status_code == 200
    assert listed.json()["envs"] == []

    put = client.put("/api/envs", json={"MY_KEY": "secret-123"})
    assert put.status_code == 200
    body = put.json()
    assert body["envs"] == [{"key": "MY_KEY", "value": "***"}]

    # Raw value lands on disk; process env is updated too.
    stored = json.loads(Path(".agentcore/envs.json").read_text(encoding="utf-8"))
    assert stored == {"MY_KEY": "secret-123"}
    import os as _os
    assert _os.environ.get("MY_KEY") == "secret-123"

    # Masked placeholder round-trips without clobbering the stored value.
    client.put("/api/envs", json={"MY_KEY": "***", "OTHER": "v2"})
    stored = json.loads(Path(".agentcore/envs.json").read_text(encoding="utf-8"))
    assert stored["MY_KEY"] == "secret-123"
    assert stored["OTHER"] == "v2"

    deleted = client.delete("/api/envs/MY_KEY")
    assert deleted.status_code == 200
    assert [e["key"] for e in deleted.json()["envs"]] == ["OTHER"]
    assert client.delete("/api/envs/MY_KEY").status_code == 404


def test_token_usage_endpoint(client) -> None:
    # No records yet — empty aggregation.
    empty = client.get("/api/token-usage").json()
    assert empty["totals"]["total_tokens"] == 0
    assert empty["daily"] == []

    # Simulate a recorded usage (as the chat router would).
    from agentcore.runtime.token_router import record_token_usage

    record_token_usage("default", 100, 50)
    record_token_usage("default", 10, 5)

    usage = client.get("/api/token-usage", params={"agent_id": "default", "days": 7}).json()
    assert usage["totals"]["total_tokens"] == 165
    assert usage["totals"]["requests"] == 2
    assert len(usage["daily"]) == 1
    assert usage["by_agent"][0]["agent_id"] == "default"

    # Filtering by another agent yields nothing.
    other = client.get("/api/token-usage", params={"agent_id": "ghost"}).json()
    assert other["totals"]["total_tokens"] == 0


def test_stats_endpoint(client) -> None:
    client.post("/api/agents", json={"agent_id": "stats-demo"})
    stats = client.get("/api/stats").json()
    assert stats["agent_count"] >= 2  # default + stats-demo
    assert stats["session_count"] == 0
    assert stats["message_count"] == 0
    assert any(a["agent_id"] == "stats-demo" for a in stats["agents"])


def test_models_endpoint(client) -> None:
    client.post("/api/agents", json={"agent_id": "models-demo"})
    listed = client.get("/api/models").json()
    assert listed["count"] >= 1
    entry = listed["models"][0]
    assert entry["name"] == "qwen3.6-plus"
    assert "models-demo" in entry["agents"]

    # Connection test without an API key fails gracefully (400).
    test = client.post(
        "/api/models/test",
        json={"name": "qwen3.6-plus", "base_url": "https://example.invalid/v1"},
    )
    assert test.status_code in (200, 400)
