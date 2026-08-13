"""Task #40 backend tests.

Covers:

* ``PUT /api/agents/{agent_id}/model`` (success / 404 / missing fields)
* bootstrap.md deletion during chat invalidates the agent graph cache
* ``MultiAgentManager.unload_workspace`` (memory-only unload, files kept)
* default agent gets ``settings.enable_subagents: true`` (others don't,
  user-set values are never overwritten)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agentcore.api import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client running inside an isolated (temporary) data directory."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


class _FakeGraph:
    """Stand-in compiled graph whose ainvoke optionally deletes bootstrap.md."""

    def __init__(self, delete_bootstrap: Path | None = None) -> None:
        self._delete_bootstrap = delete_bootstrap

    async def ainvoke(self, input_data, config=None):
        if self._delete_bootstrap is not None:
            self._delete_bootstrap.unlink(missing_ok=True)
        return {"messages": [AIMessage(content="已完成")]}


# ---------------------------------------------------------------------------
# PUT /api/agents/{agent_id}/model
# ---------------------------------------------------------------------------


def test_put_model_success(client) -> None:
    client.post("/api/agents", json={"agent_id": "model-demo"})
    chat_state = client.app.state.chat_state
    chat_state.graph_cache["model-demo"] = _FakeGraph()

    resp = client.put(
        "/api/agents/model-demo/model",
        json={
            "provider": "openai",
            "name": "qwen3.6-plus",
            "base_url": "https://coding.dashscope.aliyuncs.com/v1",
            "api_key_env": "AGENTCORE_LLM_API_KEY",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "ok"
    assert body["agent_id"] == "model-demo"
    assert body["model"]["provider"] == "openai"
    assert body["model"]["name"] == "qwen3.6-plus"
    assert body["model"]["base_url"] == "https://coding.dashscope.aliyuncs.com/v1"
    assert body["model"]["api_key_env"] == "AGENTCORE_LLM_API_KEY"

    # Persisted into agent.json; other sections preserved.
    config = json.loads(
        Path(".agentcore/workspace/agent/model-demo/agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["model"]["name"] == "qwen3.6-plus"
    assert config["tools"] == {"enabled": [], "disabled": []}

    # Graph cache invalidated so the next chat picks up the new model.
    assert "model-demo" not in chat_state.graph_cache


def test_put_model_optional_fields_keep_existing(client) -> None:
    client.post("/api/agents", json={"agent_id": "model-opt"})

    # Omit base_url / api_key_env — existing values must survive.
    resp = client.put(
        "/api/agents/model-opt/model",
        json={"provider": "openai", "name": "qwen-max"},
    )
    assert resp.status_code == 200
    model = resp.json()["model"]
    assert model["name"] == "qwen-max"
    assert model["base_url"] == "https://coding.dashscope.aliyuncs.com/v1"
    assert model["api_key_env"] == "AGENTCORE_LLM_API_KEY"


def test_put_model_unknown_agent_404(client) -> None:
    resp = client.put(
        "/api/agents/ghost/model",
        json={"provider": "openai", "name": "qwen-max"},
    )
    assert resp.status_code == 404


def test_put_model_missing_required_fields_422(client) -> None:
    client.post("/api/agents", json={"agent_id": "model-req"})
    assert (
        client.put("/api/agents/model-req/model", json={"name": "x"}).status_code
        == 422
    )
    assert (
        client.put("/api/agents/model-req/model", json={}).status_code == 422
    )


# ---------------------------------------------------------------------------
# bootstrap.md deletion closes the first-run loop (graph invalidation)
# ---------------------------------------------------------------------------


def test_chat_bootstrap_deletion_invalidates_graph(client) -> None:
    client.post("/api/agents", json={"agent_id": "boot"})
    chat_state = client.app.state.chat_state
    ws_dir = Path(".agentcore/workspace/agent/boot")
    bootstrap = ws_dir / "bootstrap.md"
    assert bootstrap.exists()

    # The cached graph's "agent" deletes bootstrap.md during this turn.
    chat_state.graph_cache["boot"] = _FakeGraph(delete_bootstrap=bootstrap)

    resp = client.post("/api/chat", json={"agent_id": "boot", "message": "开始设置"})
    assert resp.status_code == 200
    assert not bootstrap.exists()
    # Graph cache (and tracked threads) dropped → next chat rebuilds the
    # agent without the bootstrap guidance in its system_prompt.
    assert "boot" not in chat_state.graph_cache
    assert "boot" not in chat_state.threads


def test_chat_bootstrap_kept_keeps_graph(client) -> None:
    client.post("/api/agents", json={"agent_id": "boot-keep"})
    chat_state = client.app.state.chat_state
    assert (Path(".agentcore/workspace/agent/boot-keep/bootstrap.md")).exists()

    chat_state.graph_cache["boot-keep"] = _FakeGraph()  # does not delete

    resp = client.post(
        "/api/chat", json={"agent_id": "boot-keep", "message": "你好"}
    )
    assert resp.status_code == 200
    assert "boot-keep" in chat_state.graph_cache


# ---------------------------------------------------------------------------
# MultiAgentManager.unload_workspace — memory-only unload, files preserved
# ---------------------------------------------------------------------------


def test_unload_workspace_preserves_disk_files(client) -> None:
    client.post("/api/agents", json={"agent_id": "unload-demo"})
    manager = client.app.state.agent_manager
    assert manager.is_workspace_loaded("unload-demo")

    client.portal.call(manager.unload_workspace, "unload-demo")

    # Gone from memory …
    assert not manager.is_workspace_loaded("unload-demo")
    assert manager.get_workspace("unload-demo") is None
    # … but all files stay on disk (no rmtree).
    ws_dir = Path(".agentcore/workspace/agent/unload-demo")
    assert (ws_dir / "agent.json").exists()
    assert (ws_dir / "agent.md").exists()

    # Lazy re-load works afterwards.
    ws = client.portal.call(manager.get_or_create_workspace, "unload-demo")
    assert ws is not None
    assert manager.is_workspace_loaded("unload-demo")


def test_unload_workspace_unknown_agent_is_noop(client) -> None:
    manager = client.app.state.agent_manager
    client.portal.call(manager.unload_workspace, "never-existed")  # no raise


# ---------------------------------------------------------------------------
# default agent enables subagents by default (opt-in, non-clobbering)
# ---------------------------------------------------------------------------


def test_default_agent_enable_subagents(client) -> None:
    config = json.loads(
        Path(".agentcore/workspace/agent/default/agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["settings"]["enable_subagents"] is True


def test_non_default_agent_has_no_subagents_flag(client) -> None:
    client.post("/api/agents", json={"agent_id": "plain"})
    config = json.loads(
        Path(".agentcore/workspace/agent/plain/agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert "enable_subagents" not in config.get("settings", {})


def test_user_set_enable_subagents_never_overwritten(client) -> None:
    manager = client.app.state.agent_manager
    agent_json = Path(".agentcore/workspace/agent/default/agent.json")

    # User explicitly disables subagents.
    config = json.loads(agent_json.read_text(encoding="utf-8"))
    config["settings"]["enable_subagents"] = False
    agent_json.write_text(json.dumps(config), encoding="utf-8")

    # Re-initialise the workspace (stop/unload → lazy re-load).
    client.portal.call(manager.unload_workspace, "default")
    client.portal.call(manager.get_or_create_workspace, "default")

    reloaded = json.loads(agent_json.read_text(encoding="utf-8"))
    assert reloaded["settings"]["enable_subagents"] is False
