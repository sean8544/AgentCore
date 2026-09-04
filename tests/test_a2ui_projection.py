"""A2UI projection tests.

Verifies the A2UI (Agent-to-User Interface) integration:

- ``_a2ui_surface_from_args`` assembles a valid v0.9 envelope sequence
  (``createSurface`` / ``updateComponents`` / optional ``updateDataModel``)
  and degrades to ``None`` on malformed LLM output.
- ``AgentFactory`` injects the ``send_a2ui`` tool (and prompt hint) by
  default; ``settings.enable_a2ui: false`` opts out.
- ``_append_assistant_turn`` persists projected surfaces into the session
  history so reloaded sessions can re-render the cards.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentcore.api import app
from agentcore.runtime import agent_factory as agent_factory_mod
from agentcore.runtime.agent_factory import AgentFactory
from agentcore.runtime.chat_router import (
    A2UI_BASIC_CATALOG_ID,
    _a2ui_surface_from_args,
    _append_assistant_turn,
    _normalize_a2ui_action,
)
from agentcore.runtime.workspace import DEFAULT_AGENT_JSON


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _config(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "model": dict(DEFAULT_AGENT_JSON["model"]),
        "tools": {"enabled": [], "disabled": []},
        "settings": settings or {},
    }


@pytest.fixture
def captured_create(monkeypatch):
    """Patch ``create_deep_agent`` inside the factory and capture kwargs."""
    captured: dict[str, Any] = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(agent_factory_mod, "create_deep_agent", fake_create)
    return captured


@pytest.fixture(autouse=True)
def _dummy_api_key(monkeypatch):
    monkeypatch.setenv("AGENTCORE_LLM_API_KEY", "sk-test-dummy")


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client running inside an isolated (temporary) data directory."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def _seed_session(client: TestClient, session_id: str) -> None:
    client.post("/api/agents", json={"agent_id": "demo"})
    client.app.state.store.save_session(
        session_id,
        {
            "session_id": session_id,
            "agent_id": "demo",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:01Z",
            "messages": [],
        },
    )


def _sample_components() -> list[dict[str, Any]]:
    return [
        {"id": "root", "component": "Column", "children": {"explicitList": ["msg", "ok"]}},
        {"id": "msg", "component": "Text", "text": "确认执行吗？"},
        {
            "id": "ok",
            "component": "Button",
            "child": "okLabel",
            "action": {"event": {"name": "confirm", "context": {"choice": "yes"}}},
        },
        {"id": "okLabel", "component": "Text", "text": "确认"},
    ]


# ---------------------------------------------------------------------------
# _a2ui_surface_from_args — envelope assembly
# ---------------------------------------------------------------------------


def test_surface_envelope_structure() -> None:
    surface = _a2ui_surface_from_args(
        {"surface_title": "确认", "components": _sample_components()}
    )
    assert surface is not None
    assert surface["title"] == "确认"
    assert surface["surface_id"].startswith("surf-")

    messages = surface["messages"]
    assert len(messages) == 2  # createSurface + updateComponents (no data)
    create = messages[0]["createSurface"]
    assert create["surfaceId"] == surface["surface_id"]
    assert create["catalogId"] == A2UI_BASIC_CATALOG_ID
    assert create["sendDataModel"] is True

    update = messages[1]["updateComponents"]
    assert update["surfaceId"] == surface["surface_id"]
    assert len(update["components"]) == 4
    assert all(m["version"] == "v0.9" for m in messages)


def test_surface_appends_data_model_when_provided() -> None:
    surface = _a2ui_surface_from_args(
        {
            "surface_title": "表单",
            "components": _sample_components(),
            "data": {"name": ""},
        }
    )
    assert surface is not None
    messages = surface["messages"]
    assert len(messages) == 3
    dm = messages[2]["updateDataModel"]
    assert dm["surfaceId"] == surface["surface_id"]
    assert dm["path"] == "/"
    assert dm["value"] == {"name": ""}


@pytest.mark.parametrize(
    "bad_args",
    [
        None,
        "not a dict",
        {},  # missing components
        {"components": []},  # empty list
        {"components": "nope"},  # wrong type
        {"components": [{"component": "Text"}, "x", 3]},  # no id anywhere
    ],
)
def test_surface_degrades_to_none_on_malformed_args(bad_args: Any) -> None:
    assert _a2ui_surface_from_args(bad_args) is None


def test_surface_filters_out_components_without_id() -> None:
    surface = _a2ui_surface_from_args(
        {
            "components": [
                {"id": "root", "component": "Text", "text": "hi"},
                {"component": "Text", "text": "orphan without id"},
                "garbage",
            ]
        }
    )
    assert surface is not None
    kept = surface["messages"][1]["updateComponents"]["components"]
    assert len(kept) == 1
    assert kept[0]["id"] == "root"


# ---------------------------------------------------------------------------
# _normalize_a2ui_action — repair mis-shaped button actions
# ---------------------------------------------------------------------------


def test_normalize_action_keeps_valid_event_shape() -> None:
    action = {"event": {"name": "submit", "context": {"a": {"path": "/a"}}}}
    assert _normalize_a2ui_action(action) == action


def test_normalize_action_hoists_top_level_name_and_context() -> None:
    fixed = _normalize_a2ui_action(
        {"name": "submit", "context": {"k": {"path": "/k"}}}
    )
    assert fixed == {"event": {"name": "submit", "context": {"k": {"path": "/k"}}}}


def test_normalize_action_hoists_context_sibling_of_event() -> None:
    # Shape observed from a real model output: context next to event.
    fixed = _normalize_a2ui_action(
        {"event": {"name": "submit_form"}, "context": {"name": {"path": "/name"}}}
    )
    assert fixed == {
        "event": {
            "name": "submit_form",
            "context": {"name": {"path": "/name"}},
        }
    }


def test_normalize_action_event_keys_win_over_strays() -> None:
    fixed = _normalize_a2ui_action(
        {"event": {"name": "kept"}, "name": "dropped"}
    )
    assert fixed == {"event": {"name": "kept"}}


def test_normalize_action_ignores_non_event_actions() -> None:
    action = {"functionCall": {"name": "noop", "args": {}}}
    assert _normalize_a2ui_action(action) == action
    assert _normalize_a2ui_action("not a dict") == "not a dict"


def test_surface_normalizes_component_actions_without_mutating_input() -> None:
    bad_action = {"event": {"name": "submit"}, "context": {"k": {"path": "/k"}}}
    args = {
        "components": [
            {"id": "root", "component": "Button", "child": "lbl", "action": bad_action},
            {"id": "lbl", "component": "Text", "text": "提交"},
        ]
    }
    surface = _a2ui_surface_from_args(args)
    assert surface is not None
    kept = surface["messages"][1]["updateComponents"]["components"]
    assert kept[0]["action"] == {
        "event": {"name": "submit", "context": {"k": {"path": "/k"}}}
    }
    # The caller's args dict must stay untouched (dedupe keys rely on it).
    assert bad_action == {"event": {"name": "submit"}, "context": {"k": {"path": "/k"}}}


def test_surface_maps_css_layout_aliases() -> None:
    surface = _a2ui_surface_from_args(
        {
            "components": [
                {
                    "id": "root",
                    "component": "Row",
                    "children": ["a"],
                    "justify": "flex-end",
                    "align": "flex-start",
                },
                {"id": "a", "component": "Text", "text": "x"},
            ]
        }
    )
    assert surface is not None
    kept = surface["messages"][1]["updateComponents"]["components"]
    assert kept[0]["justify"] == "end"
    assert kept[0]["align"] == "start"


def test_surface_keeps_legal_layout_values() -> None:
    surface = _a2ui_surface_from_args(
        {
            "components": [
                {
                    "id": "root",
                    "component": "Column",
                    "children": ["a"],
                    "justify": "spaceBetween",
                    "align": "stretch",
                },
                {"id": "a", "component": "Text", "text": "x"},
            ]
        }
    )
    assert surface is not None
    kept = surface["messages"][1]["updateComponents"]["components"]
    assert kept[0]["justify"] == "spaceBetween"
    assert kept[0]["align"] == "stretch"


# ---------------------------------------------------------------------------
# AgentFactory — opt-in tool injection
# ---------------------------------------------------------------------------


def _tool_names(captured: dict[str, Any]) -> list[str]:
    return [getattr(t, "name", None) for t in captured.get("tools") or []]


def test_send_a2ui_tool_added_by_default(captured_create) -> None:
    AgentFactory().create_agent(_config())
    assert "send_a2ui" in _tool_names(captured_create)


def test_send_a2ui_absent_when_explicitly_disabled(captured_create) -> None:
    AgentFactory().create_agent(_config(settings={"enable_a2ui": False}))
    assert "send_a2ui" not in _tool_names(captured_create)


def test_a2ui_prompt_hint_follows_switch(captured_create) -> None:
    AgentFactory().create_agent(_config())
    prompt = captured_create.get("system_prompt") or captured_create.get("instructions") or ""
    assert "send_a2ui" in prompt

    AgentFactory().create_agent(_config(settings={"enable_a2ui": False}))
    prompt_off = captured_create.get("system_prompt") or captured_create.get("instructions") or ""
    assert "send_a2ui" not in prompt_off


def test_send_a2ui_tool_body_is_control_plane_only() -> None:
    tool = agent_factory_mod._create_send_a2ui_tool()
    assert tool.name == "send_a2ui"
    result = tool.invoke(
        {"surface_title": "t", "components": [{"id": "root", "component": "Text"}]}
    )
    # The tool only acknowledges rendering; it never performs file/IO work.
    assert isinstance(result, str) and "rendered" in result


# ---------------------------------------------------------------------------
# History persistence — surfaces survive reload
# ---------------------------------------------------------------------------


def test_append_assistant_turn_persists_a2ui_surfaces(client) -> None:
    _seed_session(client, "sess-a2ui")
    surface = _a2ui_surface_from_args(
        {"surface_title": "确认", "components": _sample_components()}
    )
    _append_assistant_turn(
        client.app.state.store,
        "sess-a2ui",
        ["请看下面的卡片"],
        [],
        [],
        None,
        [],
        None,
        a2ui_surfaces=[surface],
    )

    msg = client.app.state.store.load_session("sess-a2ui")["messages"][-1]
    assert msg["content"] == "请看下面的卡片"
    stored = msg["a2ui_surfaces"]
    assert len(stored) == 1
    assert stored[0]["surface_id"] == surface["surface_id"]
    assert stored[0]["messages"] == surface["messages"]


def test_append_assistant_turn_without_surfaces_keeps_history_clean(client) -> None:
    _seed_session(client, "sess-a2ui-none")
    _append_assistant_turn(
        client.app.state.store, "sess-a2ui-none", ["普通回复"], [], [], None, [], None
    )

    msg = client.app.state.store.load_session("sess-a2ui-none")["messages"][-1]
    assert "a2ui_surfaces" not in msg
