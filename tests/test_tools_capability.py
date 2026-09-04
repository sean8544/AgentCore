"""Tests for the built-in + capability tools management router.

Covers ``GET /api/agents/{id}/tools`` and ``PUT /api/agents/{id}/tools/{name}``
for the additive ``send_a2ui`` capability tool (backed by
``settings.enable_a2ui``) alongside the existing built-in tool behaviour
(backed by ``tools.disabled``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcore.api import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def _read_config(agent_id: str) -> dict:
    return json.loads(
        Path(f".agentcore/workspace/agent/{agent_id}/agent.json").read_text(
            encoding="utf-8"
        )
    )


# ---------------------------------------------------------------------------
# GET — send_a2ui is listed as a capability tool, on by default
# ---------------------------------------------------------------------------


def test_list_tools_includes_send_a2ui_capability(client) -> None:
    client.post("/api/agents", json={"agent_id": "tools-cap"})
    resp = client.get("/api/agents/tools-cap/tools")
    assert resp.status_code == 200
    tools = {t["name"]: t for t in resp.json()["tools"]}

    assert "send_a2ui" in tools
    assert tools["send_a2ui"]["builtin"] is False
    # default-on: no explicit settings.enable_a2ui yet → enabled
    assert tools["send_a2ui"]["enabled"] is True
    # built-in tools are still reported and flagged builtin
    assert tools["read_file"]["builtin"] is True


# ---------------------------------------------------------------------------
# PUT — toggling send_a2ui writes settings.enable_a2ui (not tools.disabled)
# ---------------------------------------------------------------------------


def test_toggle_send_a2ui_persists_settings_flag(client) -> None:
    client.post("/api/agents", json={"agent_id": "tools-toggle"})

    resp = client.put(
        "/api/agents/tools-toggle/tools/send_a2ui", json={"enabled": False}
    )
    assert resp.status_code == 200
    assert resp.json() == {"result": "ok", "tool": "send_a2ui", "enabled": False}

    config = _read_config("tools-toggle")
    assert config["settings"]["enable_a2ui"] is False
    # must NOT leak into the built-in deny list
    assert "send_a2ui" not in (config.get("tools", {}).get("disabled") or [])

    # reflected in the listing
    tools = {t["name"]: t for t in client.get("/api/agents/tools-toggle/tools").json()["tools"]}
    assert tools["send_a2ui"]["enabled"] is False

    # toggle back on
    client.put("/api/agents/tools-toggle/tools/send_a2ui", json={"enabled": True})
    assert _read_config("tools-toggle")["settings"]["enable_a2ui"] is True


def test_toggle_send_a2ui_invalidates_graph_cache(client) -> None:
    client.post("/api/agents", json={"agent_id": "tools-inval"})

    class _FakeGraph:  # noqa: D401 - trivial stand-in
        pass

    chat_state = client.app.state.chat_state
    chat_state.graph_cache["tools-inval"] = _FakeGraph()

    client.put("/api/agents/tools-inval/tools/send_a2ui", json={"enabled": False})
    assert "tools-inval" not in chat_state.graph_cache


# ---------------------------------------------------------------------------
# PUT — built-in tool still uses tools.disabled; unknown tool → 404
# ---------------------------------------------------------------------------


def test_toggle_builtin_tool_uses_disabled_list(client) -> None:
    client.post("/api/agents", json={"agent_id": "tools-builtin"})
    resp = client.put(
        "/api/agents/tools-builtin/tools/grep", json={"enabled": False}
    )
    assert resp.status_code == 200
    assert "grep" in _read_config("tools-builtin")["tools"]["disabled"]


def test_toggle_unknown_tool_404(client) -> None:
    client.post("/api/agents", json={"agent_id": "tools-404"})
    resp = client.put(
        "/api/agents/tools-404/tools/not_a_tool", json={"enabled": True}
    )
    assert resp.status_code == 404
