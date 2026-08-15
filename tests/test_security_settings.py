"""Global security settings tests.

Covers:
- ``GET/PUT /api/security/settings`` (defaults, persistence, validation,
  graph-cache invalidation)
- ``apply_global_approval`` merging — global approval rules apply to every
  agent while agent-level rules win on conflicts
- ``_resolve_agent_graph`` passes the merged rules into graph building
- ``_stream_chat_sse`` keeps the partial turn when the stream is cancelled
  (stop-conversation support)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk

from agentcore.api import app
from agentcore.runtime import chat_router
from agentcore.runtime import security_router


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client running inside an isolated (temporary) data directory."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _dummy_api_key(monkeypatch):
    """Default agent.json carries a DashScope base_url; a key is required to
    build subagent specs in tests (same pattern as other suites)."""
    monkeypatch.setenv("AGENTCORE_LLM_API_KEY", "sk-test-dummy")


class _FakeGraph:
    """Minimal stand-in compiled graph."""

    async def ainvoke(self, input_data, config=None):
        return {"messages": []}


def _security_file() -> Path:
    return Path(".agentcore/security.json")


# ---------------------------------------------------------------------------
# GET / PUT /api/security/settings
# ---------------------------------------------------------------------------


def test_get_settings_defaults(client) -> None:
    resp = client.get("/api/security/settings")
    assert resp.status_code == 200
    assert resp.json() == {"approval": {"enabled": False, "tools": []}}


def test_put_settings_roundtrip(client) -> None:
    resp = client.put(
        "/api/security/settings",
        json={"approval": {"enabled": True, "tools": ["delete", "write_file"]}},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "approval": {"enabled": True, "tools": ["delete", "write_file"]},
    }
    # Persisted on disk.
    stored = json.loads(_security_file().read_text(encoding="utf-8"))
    assert stored["approval"]["tools"] == ["delete", "write_file"]
    # Survives a fresh read.
    assert client.get("/api/security/settings").json() == resp.json()


def test_put_settings_validation(client) -> None:
    assert (
        client.put("/api/security/settings", json={}).status_code == 400
    )
    assert (
        client.put(
            "/api/security/settings",
            json={"approval": {"enabled": True, "tools": "delete"}},
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/security/settings",
            json={"approval": {"enabled": True, "tools": [""]}},
        ).status_code
        == 400
    )


def test_put_settings_dedupes_and_trims_tools(client) -> None:
    resp = client.put(
        "/api/security/settings",
        json={"approval": {"enabled": True, "tools": [" delete ", "delete"]}},
    )
    assert resp.status_code == 200
    assert resp.json()["approval"]["tools"] == ["delete"]


def test_put_settings_invalidates_graph_cache(client) -> None:
    client.post("/api/agents", json={"agent_id": "sec-demo"})
    chat_state = client.app.state.chat_state
    chat_state.graph_cache["sec-demo"] = object()
    client.put(
        "/api/security/settings",
        json={"approval": {"enabled": True, "tools": ["delete"]}},
    )
    assert "sec-demo" not in chat_state.graph_cache


# ---------------------------------------------------------------------------
# Global rule merging
# ---------------------------------------------------------------------------


def test_load_approval_interrupt_rules_disabled_by_default(client) -> None:
    assert security_router.load_approval_interrupt_rules() == []


def test_load_approval_interrupt_rules_enabled(client) -> None:
    client.put(
        "/api/security/settings",
        json={"approval": {"enabled": True, "tools": ["delete", "execute"]}},
    )
    rules = security_router.load_approval_interrupt_rules()
    assert rules == [
        {"tool_name": "delete", "require_approval": True},
        {"tool_name": "execute", "require_approval": True},
    ]


def test_apply_global_approval_merges(client) -> None:
    client.put(
        "/api/security/settings",
        json={"approval": {"enabled": True, "tools": ["delete", "write_file"]}},
    )
    settings = {
        "interrupt_rules": [
            {"tool_name": "write_file", "require_approval": True},
        ]
    }
    merged = security_router.apply_global_approval(settings)
    # Agent-level write_file wins; global delete fills the gap.
    assert [r["tool_name"] for r in merged["interrupt_rules"]] == [
        "write_file",
        "delete",
    ]


def test_apply_global_approval_disabled_keeps_agent_rules(client) -> None:
    settings = {
        "interrupt_rules": [
            {"tool_name": "write_file", "require_approval": True},
        ]
    }
    merged = security_router.apply_global_approval(settings)
    assert merged is settings


def test_apply_global_approval_empty_settings(client) -> None:
    client.put(
        "/api/security/settings",
        json={"approval": {"enabled": True, "tools": ["delete"]}},
    )
    merged = security_router.apply_global_approval({})
    assert merged["interrupt_rules"] == [
        {"tool_name": "delete", "require_approval": True},
    ]


# ---------------------------------------------------------------------------
# Graph building honours the global rules
# ---------------------------------------------------------------------------


def test_resolve_agent_graph_merges_global_rules(client) -> None:
    client.post("/api/agents", json={"agent_id": "sec-merge"})
    client.put(
        "/api/security/settings",
        json={"approval": {"enabled": True, "tools": ["delete"]}},
    )

    captured: dict = {}

    class _RecordingFactory:
        def create_agent(self, agent_config, **kwargs):
            captured["config"] = agent_config
            return _FakeGraph()

    manager = client.app.state.agent_manager
    workspace = manager.get_workspace("sec-merge")
    # Pre-seed the checkpointer so the sync test avoids creating an
    # AsyncSqliteSaver (which requires a running event loop).
    chat_state = chat_router.ChatState()
    chat_state.checkpointer = object()
    chat_router._resolve_agent_graph(
        "sec-merge",
        workspace,
        factory=_RecordingFactory(),
        manager=manager,
        chat_state=chat_state,
    )
    settings = captured["config"]["settings"]
    tools = [r["tool_name"] for r in settings["interrupt_rules"]]
    assert "delete" in tools


# ---------------------------------------------------------------------------
# Stream cancellation keeps the partial turn
# ---------------------------------------------------------------------------


class _SlowStreamGraph:
    """Graph whose astream yields one chunk then hangs forever."""

    async def astream(self, input_data, config=None, stream_mode=None):
        yield ("messages", (AIMessageChunk(content="部分回复"), {}))
        await asyncio.sleep(3600)


def test_stream_cancel_persists_partial_turn(client) -> None:
    """Stopping the conversation keeps whatever was already generated."""
    store = client.app.state.store
    session_id = "cancel-session"
    store.save_session(
        session_id,
        {
            "session_id": session_id,
            "agent_id": "sec-demo",
            "created_at": chat_router._now_iso(),
            "updated_at": chat_router._now_iso(),
            "messages": [],
        },
    )

    gen = chat_router._stream_chat_sse(
        _SlowStreamGraph(),
        session_id,
        "sec-demo",
        "测试取消",
        chat_router.ChatState(),
        store=store,
    )

    async def consume_then_abort() -> dict:
        first = await gen.__anext__()
        # Simulate the client pressing stop — closes the async generator.
        await gen.aclose()
        return json.loads(first.split("data: ", 1)[1])

    first = asyncio.run(consume_then_abort())
    assert first["content"] == "部分回复"

    session = store.load_session(session_id)
    assert session is not None
    assistant = [
        m for m in session.get("messages", []) if m.get("role") == "assistant"
    ]
    assert len(assistant) == 1
    assert "部分回复" in assistant[0]["content"]
