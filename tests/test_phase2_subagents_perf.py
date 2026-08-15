"""Regression tests for Task #35 (phase 2).

Covers:

- ``SubAgentRegistry`` aggregation of loaded workspaces.
- ``chat_router`` opt-in subagent injection (``settings.enable_subagents``).
- ``AgentFactory`` subagents pass-through to ``create_deep_agent``.
- ``ChatManager`` per-session file persistence (``sessions/{id}.json`` +
  ``index.json``) and legacy ``sessions.json`` migration.
- Model client caching (incl. API-key-change invalidation).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentcore.runtime import agent_factory as agent_factory_mod
from agentcore.runtime import chat_router
from agentcore.runtime.agent_factory import AgentFactory
from agentcore.runtime.subagent_registry import SubAgentRegistry
from agentcore.runtime.workspace import (
    DEFAULT_AGENT_JSON,
    ChatManager,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _config(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "model": dict(DEFAULT_AGENT_JSON["model"]),
        "tools": {"enabled": [], "disabled": []},
        "settings": settings or {},
    }


@pytest.fixture(autouse=True)
def _dummy_api_key(monkeypatch):
    monkeypatch.setenv("AGENTCORE_LLM_API_KEY", "sk-test-dummy")


@pytest.fixture(autouse=True)
def _clear_graph_cache():
    chat_router._default_chat_state.graph_cache.clear()
    yield
    chat_router._default_chat_state.graph_cache.clear()


def _fake_workspace(prompt: str = "I am a helper.") -> SimpleNamespace:
    return SimpleNamespace(
        workspace_dir=Path("ws/dir"),
        read_agent_config=lambda: _config(),
        get_system_prompt=lambda: prompt,
    )


def _fake_manager(workspaces: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(_workspaces=workspaces)


class _CapturingFactory:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def create_agent(self, agent_config, **kwargs):
        self.calls.append({"agent_config": agent_config, **kwargs})
        return object()


# ---------------------------------------------------------------------------
# SubAgentRegistry
# ---------------------------------------------------------------------------


def test_registry_aggregates_workspaces_and_excludes_self():
    manager = _fake_manager({
        "alice": _fake_workspace("Alice prompt"),
        "bob": _fake_workspace("Bob prompt"),
    })
    registry = SubAgentRegistry(manager)

    subagents = registry.get_subagents(exclude_agent_id="alice")
    assert [s["name"] for s in subagents] == ["bob"]
    spec = subagents[0]
    assert spec["description"] == "Agent 'bob'"
    assert spec["system_prompt"] == "Bob prompt"


def test_registry_empty_prompt_falls_back():
    manager = _fake_manager({"solo": _fake_workspace("")})
    specs = SubAgentRegistry(manager).get_subagents()
    assert specs[0]["system_prompt"]  # non-empty fallback


# ---------------------------------------------------------------------------
# chat_router — opt-in subagent injection
# ---------------------------------------------------------------------------


def test_subagents_injected_when_enabled(monkeypatch):
    monkeypatch.setattr(chat_router, "get_checkpointer", lambda chat_state: object())
    manager = _fake_manager({
        "main": _fake_workspace(),
        "helper": _fake_workspace("Helper prompt"),
    })
    workspace = SimpleNamespace(
        workspace_dir=Path("ws/dir"),
        read_agent_config=lambda: _config({"enable_subagents": True}),
    )
    factory = _CapturingFactory()

    chat_router._resolve_agent_graph(
        "main", workspace, factory=factory, manager=manager
    )

    subagents = factory.calls[0]["subagents"]
    assert subagents and [s["name"] for s in subagents] == ["helper"]


def test_subagents_not_injected_by_default(monkeypatch):
    monkeypatch.setattr(chat_router, "get_checkpointer", lambda chat_state: object())
    manager = _fake_manager({"main": _fake_workspace(), "helper": _fake_workspace()})
    workspace = SimpleNamespace(
        workspace_dir=Path("ws/dir"),
        read_agent_config=lambda: _config(),  # no enable_subagents
    )
    factory = _CapturingFactory()

    chat_router._resolve_agent_graph(
        "main", workspace, factory=factory, manager=manager
    )
    assert factory.calls[0]["subagents"] is None


# ---------------------------------------------------------------------------
# AgentFactory — subagents pass-through
# ---------------------------------------------------------------------------


def test_factory_passes_subagents_to_create_deep_agent(monkeypatch):
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        agent_factory_mod,
        "create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    spec = {"name": "helper", "description": "d", "system_prompt": "p"}
    AgentFactory().create_agent(_config(), subagents=[spec])
    assert captured.get("subagents") == [spec]


def test_factory_omits_subagents_when_absent(monkeypatch):
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        agent_factory_mod,
        "create_deep_agent",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    AgentFactory().create_agent(_config())
    assert "subagents" not in captured


# ---------------------------------------------------------------------------
# ChatManager — per-session file layout
# ---------------------------------------------------------------------------


def test_chat_manager_per_session_files_and_index(tmp_path):
    cm = ChatManager(workspace_id="w", _persist_dir=tmp_path)
    cm.create_session("s1", agent_id="a")
    cm.add_message("s1", "user", "hello")
    cm.add_message("s1", "assistant", "hi there")
    cm.create_session("s2", agent_id="a")

    # Layout: one file per session + a metadata index.
    assert (tmp_path / "s1.json").exists()
    assert (tmp_path / "s2.json").exists()
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["s1"]["message_count"] == 2
    assert index["s2"]["message_count"] == 0
    assert not (tmp_path / "sessions.json").exists()

    # Reloading from disk restores the sessions.
    cm2 = ChatManager(workspace_id="w", _persist_dir=tmp_path)
    cm2.load()
    assert cm2.get_history("s1") == cm.get_history("s1")
    assert set(cm2.list_sessions()) == {"s1", "s2"}


def test_chat_manager_add_message_only_touches_own_file(tmp_path):
    cm = ChatManager(workspace_id="w", _persist_dir=tmp_path)
    cm.create_session("s1")
    cm.create_session("s2")
    (tmp_path / "s2.json").write_text("SENTINEL", encoding="utf-8")

    cm.add_message("s1", "user", "ping")

    # The untouched session's file must not be rewritten.
    assert (tmp_path / "s2.json").read_text(encoding="utf-8") == "SENTINEL"


def test_chat_manager_delete_session_removes_file(tmp_path):
    cm = ChatManager(workspace_id="w", _persist_dir=tmp_path)
    cm.create_session("s1")
    cm.delete_session("s1")
    assert not (tmp_path / "s1.json").exists()
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert "s1" not in index


def test_chat_manager_migrates_legacy_sessions_json(tmp_path):
    legacy = {
        "sessions": {
            "old1": {
                "session_id": "old1",
                "agent_id": "a",
                "created_at": "t0",
                "updated_at": "t1",
                "messages": [{"role": "user", "content": "hi"}],
            }
        }
    }
    (tmp_path / "sessions.json").write_text(
        json.dumps(legacy), encoding="utf-8"
    )

    cm = ChatManager(workspace_id="w", _persist_dir=tmp_path)
    cm.load()

    assert cm.get_history("old1")[0]["content"] == "hi"
    assert (tmp_path / "old1.json").exists()
    assert not (tmp_path / "sessions.json").exists()  # migrated away


def test_chat_manager_session_id_path_traversal_safe(tmp_path):
    cm = ChatManager(workspace_id="w", _persist_dir=tmp_path)
    cm.create_session("../../evil")
    # The file must stay inside the sessions directory.
    assert not (tmp_path.parent / "evil.json").exists()
    assert cm.get_history("../../evil") == []


# ---------------------------------------------------------------------------
# ControlPlaneStore — per-session file layout (chat API persistence path)
# ---------------------------------------------------------------------------


def test_store_sessions_per_file_layout(tmp_path):
    from agentcore.repository import ControlPlaneStore

    store = ControlPlaneStore(data_dir=tmp_path)
    store.save_session("s1", {"session_id": "s1", "agent_id": "a", "messages": []})
    store.save_session("s2", {"session_id": "s2", "agent_id": "b", "messages": []})

    sessions_dir = tmp_path / "sessions"
    assert (sessions_dir / "s1.json").exists()
    assert (sessions_dir / "s2.json").exists()
    assert (sessions_dir / "sessions_index.json").exists()
    assert not (tmp_path / "sessions.json").exists()

    # Reload restores sessions from the per-file layout.
    store2 = ControlPlaneStore(data_dir=tmp_path)
    assert set(store2.load_sessions()) == {"s1", "s2"}

    # Deleting removes the file.
    store2.delete_session("s1")
    assert not (sessions_dir / "s1.json").exists()


def test_store_migrates_legacy_sessions_json(tmp_path):
    from agentcore.repository import ControlPlaneStore

    legacy = {
        "sessions": {
            "old": {"session_id": "old", "agent_id": "a", "messages": []}
        }
    }
    (tmp_path / "sessions.json").write_text(json.dumps(legacy), encoding="utf-8")

    store = ControlPlaneStore(data_dir=tmp_path)
    assert store.load_session("old") is not None
    assert (tmp_path / "sessions" / "old.json").exists()
    assert not (tmp_path / "sessions.json").exists()


# ---------------------------------------------------------------------------
# files_router — async refactor keeps endpoint behavior intact
# ---------------------------------------------------------------------------


def test_files_api_still_works_after_async_refactor(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from agentcore.api import app

    monkeypatch.chdir(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/agents", json={"agent_id": "files-demo"}).status_code == 200

        tree = client.get("/api/agents/files-demo/files/tree")
        assert tree.status_code == 200
        names = {item["name"] for item in tree.json()["items"]}
        assert "agent.md" in names

        put = client.put(
            "/api/agents/files-demo/files/content",
            params={"path": "notes.txt"},
            json={"content": "hello files"},
        )
        assert put.status_code == 200

        got = client.get(
            "/api/agents/files-demo/files/content", params={"path": "notes.txt"}
        )
        assert got.status_code == 200
        assert got.json()["content"] == "hello files"

        # Path traversal is still rejected.
        evil = client.get(
            "/api/agents/files-demo/files/content", params={"path": "../../x"}
        )
        assert evil.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Model client caching
# ---------------------------------------------------------------------------


def test_model_cache_reuses_and_invalidates_on_key_change(monkeypatch):
    from agentcore.runtime import model_factory as mf

    mf.clear_model_cache()
    monkeypatch.setenv("AGENTCORE_LLM_API_KEY", "sk-one")
    cfg = {"provider": "openai", "name": "qwen3.6-plus"}

    first = mf.build_chat_model(dict(cfg))
    second = mf.build_chat_model(dict(cfg))
    assert first is second  # cached

    # Rotating the API key must produce a fresh client.
    monkeypatch.setenv("AGENTCORE_LLM_API_KEY", "sk-two")
    third = mf.build_chat_model(dict(cfg))
    assert third is not first
    mf.clear_model_cache()
