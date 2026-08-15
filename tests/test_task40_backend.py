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


@pytest.fixture(autouse=True)
def _dummy_api_key(monkeypatch):
    """Default agent.json carries a DashScope base_url; a key is required to
    build subagent specs in tests (same pattern as other suites)."""
    monkeypatch.setenv("AGENTCORE_LLM_API_KEY", "sk-test-dummy")


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


# ---------------------------------------------------------------------------
# PUT /api/agents/{agent_id}/settings — system_prompt 编辑 + 拓扑返回
# ---------------------------------------------------------------------------


def test_put_settings_updates_system_prompt(client) -> None:
    client.post("/api/agents", json={"agent_id": "settings-demo"})
    chat_state = client.app.state.chat_state
    chat_state.graph_cache["settings-demo"] = _FakeGraph()

    resp = client.put(
        "/api/agents/settings-demo/settings",
        json={"system_prompt": "你是新人格", "description": "新描述"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "ok"
    assert body["settings"]["system_prompt"] == "你是新人格"
    assert body["settings"]["description"] == "新描述"

    # Persisted into agent.json; graph cache invalidated for rebuild.
    config = json.loads(
        Path(".agentcore/workspace/agent/settings-demo/agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["settings"]["system_prompt"] == "你是新人格"
    assert "settings-demo" not in chat_state.graph_cache

    # GET detail returns settings + empty subagent topology.
    detail = client.get("/api/agents/settings-demo").json()
    assert detail["settings"]["system_prompt"] == "你是新人格"
    assert detail["subagents"] == []


def test_put_settings_enable_subagents_exposes_topology(client) -> None:
    client.post("/api/agents", json={"agent_id": "supervisor"})
    client.post("/api/agents", json={"agent_id": "worker"})

    resp = client.put(
        "/api/agents/supervisor/settings",
        json={"enable_subagents": True, "description": "主管"},
    )
    assert resp.status_code == 200

    detail = client.get("/api/agents/supervisor").json()
    names = [s["name"] for s in detail["subagents"]]
    assert "worker" in names
    assert "supervisor" not in names  # never delegates to itself

    listing = client.get("/api/agents").json()
    sup = next(a for a in listing if a["agent_id"] == "supervisor")
    assert sup["enable_subagents"] is True
    assert [s["name"] for s in sup["subagents"]] == names


def test_subagent_topology_honours_whitelist(client) -> None:
    """Changing settings.subagent_ids updates the exposed topology list.

    Regression test: the console topology (GET /api/agents…/subagents)
    must apply the same ``subagent_ids`` whitelist the runtime uses at
    graph-build time instead of listing every loaded agent.
    """
    client.post("/api/agents", json={"agent_id": "boss"})
    client.post("/api/agents", json={"agent_id": "worker-a"})
    client.post("/api/agents", json={"agent_id": "worker-b"})

    # No whitelist → all other agents are listed (the app also seeds a
    # ``default`` agent, so assert inclusion rather than exact equality).
    resp = client.put(
        "/api/agents/boss/settings",
        json={"enable_subagents": True},
    )
    assert resp.status_code == 200
    names = [
        s["name"]
        for s in client.get("/api/agents/boss").json()["subagents"]
    ]
    assert {"worker-a", "worker-b"} <= set(names)
    assert "boss" not in names

    # Restrict delegation to worker-a only.
    resp = client.put(
        "/api/agents/boss/settings",
        json={"subagent_ids": ["worker-a"]},
    )
    assert resp.status_code == 200
    names = [
        s["name"]
        for s in client.get("/api/agents/boss").json()["subagents"]
    ]
    assert names == ["worker-a"]

    # The list endpoint agrees with the detail endpoint.
    listing = client.get("/api/agents").json()
    boss = next(a for a in listing if a["agent_id"] == "boss")
    assert [s["name"] for s in boss["subagents"]] == ["worker-a"]

    # Clearing the whitelist restores the full list.
    resp = client.put(
        "/api/agents/boss/settings", json={"subagent_ids": []}
    )
    assert resp.status_code == 200
    names = [
        s["name"]
        for s in client.get("/api/agents/boss").json()["subagents"]
    ]
    assert {"worker-a", "worker-b"} <= set(names)


def test_put_settings_unknown_agent_404(client) -> None:
    resp = client.put(
        "/api/agents/ghost/settings",
        json={"system_prompt": "x"},
    )
    assert resp.status_code == 404


def test_put_settings_interrupt_rules_roundtrip(client) -> None:
    """PUT settings persists HITL interrupt_rules into agent.json."""
    client.post("/api/agents", json={"agent_id": "hitl-demo"})

    resp = client.put(
        "/api/agents/hitl-demo/settings",
        json={
            "interrupt_rules": [
                {"tool_name": "delete", "require_approval": True},
                {"tool_name": "write_file", "require_approval": True},
            ]
        },
    )
    assert resp.status_code == 200
    rules = resp.json()["settings"]["interrupt_rules"]
    assert {r["tool_name"] for r in rules} == {"delete", "write_file"}

    config = json.loads(
        Path(".agentcore/workspace/agent/hitl-demo/agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["settings"]["interrupt_rules"] == rules

    # GET detail round-trips the rules.
    detail = client.get("/api/agents/hitl-demo").json()
    assert detail["settings"]["interrupt_rules"] == rules


def test_put_settings_interrupt_rules_empty_clears(client) -> None:
    """An empty interrupt_rules list clears HITL rules."""
    client.post("/api/agents", json={"agent_id": "hitl-clear"})
    client.put(
        "/api/agents/hitl-clear/settings",
        json={"interrupt_rules": [{"tool_name": "delete", "require_approval": True}]},
    )
    resp = client.put(
        "/api/agents/hitl-clear/settings",
        json={"interrupt_rules": []},
    )
    assert resp.status_code == 200
    assert resp.json()["settings"]["interrupt_rules"] == []

    config = json.loads(
        Path(".agentcore/workspace/agent/hitl-clear/agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["settings"]["interrupt_rules"] == []


def test_create_agent_with_interrupt_rules(client) -> None:
    """POST /agents accepts interrupt_rules as a create-time preset."""
    resp = client.post(
        "/api/agents",
        json={
            "agent_id": "hitl-new",
            "interrupt_rules": [{"tool_name": "delete", "require_approval": True}],
        },
    )
    assert resp.status_code == 200

    config = json.loads(
        Path(".agentcore/workspace/agent/hitl-new/agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["settings"]["interrupt_rules"] == [
        {"tool_name": "delete", "require_approval": True}
    ]


# ---------------------------------------------------------------------------
# Delegations API — subagent 能看到被委派的对话记录
# ---------------------------------------------------------------------------


def test_delegations_roundtrip(client) -> None:
    client.post("/api/agents", json={"agent_id": "deleg-target"})
    store = client.app.state.store
    store.append_delegation(
        "deleg-target",
        {
            "parent_agent_id": "supervisor",
            "task_description": "帮我算一下",
            "timestamp": "2026-08-14T10:00:00",
        },
    )

    resp = client.get("/api/agents/deleg-target/delegations")
    assert resp.status_code == 200
    records = resp.json()["delegations"]
    assert len(records) == 1
    assert records[0]["parent_agent_id"] == "supervisor"
    assert records[0]["task_description"] == "帮我算一下"


def test_delegations_unknown_agent_empty(client) -> None:
    client.post("/api/agents", json={"agent_id": "no-deleg"})
    resp = client.get("/api/agents/no-deleg/delegations")
    assert resp.status_code == 200
    assert resp.json()["delegations"] == []


# ---------------------------------------------------------------------------
# SSE 投影：subagents（task 委派）与 todo（write_todos）事件
# ---------------------------------------------------------------------------


class _FakeStreamGraph:
    """astream yielding updates with task delegation + write_todos calls."""

    async def astream(self, input_data, config=None, stream_mode=None):
        # Node update carrying a task tool call (subagent delegation).
        task_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {
                        "subagent_type": "worker",
                        "description": "计算报表",
                    },
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        )
        yield ("updates", {"task": {"messages": [task_msg]}})
        # Node update carrying a write_todos call (task plan).
        todo_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_todos",
                    "args": {
                        "todos": [
                            {"title": "拆解需求", "status": "pending"},
                            {"title": "实施", "status": "in_progress"},
                        ]
                    },
                    "id": "call_2",
                    "type": "tool_call",
                }
            ],
        )
        yield ("updates", {"task": {"messages": [todo_msg]}})
        yield ("messages", (AIMessage(content="最终回复"), {}))
        yield ("values", {"messages": []})


def test_sse_async_projects_subagent_and_todo_events() -> None:
    import asyncio

    from agentcore.runtime.chat_router import ChatState, _stream_chat_sse

    async def consume() -> list[tuple[str, dict]]:
        events: list[tuple[str, dict]] = []
        async for event in _stream_chat_sse(
            _FakeStreamGraph(), "s1", "main-agent", "你好", ChatState()
        ):
            # event is an SSE string: "event: X\ndata: {...}\n\n"
            event_type = None
            data = None
            for line in event.split("\n"):
                if line.startswith("event: "):
                    event_type = line[len("event: "):].strip()
                elif line.startswith("data: "):
                    import json as _json

                    data = _json.loads(line[len("data: "):])
            events.append((event_type or "", data or {}))
        return events

    events = asyncio.run(consume())
    types = [e[0] for e in events]

    # Subagent delegation surfaced (task tool call).
    assert "subagents" in types
    subagent_events = [e[1] for e in events if e[0] == "subagents"]
    task_calls = [
        tc
        for ev in subagent_events
        for tc in ev.get("tool_calls", [])
        if tc.get("name") == "task"
    ]
    assert len(task_calls) == 1
    assert task_calls[0]["args"]["subagent_type"] == "worker"

    # Todo plan projected (write_todos tool call).
    # With real-time status tracking, we may emit multiple todo events:
    # 1. Initial todos with first item marked as in_progress
    # 2. Status updates as tools execute (in_progress -> completed, next -> in_progress)
    assert "todo" in types
    todo_events = [e[1] for e in events if e[0] == "todo"]
    assert len(todo_events) >= 1
    # First todo event should have the original todos.
    assert [t["title"] for t in todo_events[0]["todos"]] == ["拆解需求", "实施"]
    # If there are multiple events, verify status progression.
    if len(todo_events) > 1:
        # Later events should show status updates (completed/in_progress).
        last_todo_event = todo_events[-1]["todos"]
        statuses = [t.get("status") for t in last_todo_event]
        assert "completed" in statuses or "in_progress" in statuses

    # Stream completes.
    assert "done" in types


def test_sse_persists_delegation_record(client) -> None:
    """task 委派会写入被委派 agent 的 delegation 记录。"""
    import asyncio

    from agentcore.runtime.chat_router import ChatState, _stream_chat_sse

    client.post("/api/agents", json={"agent_id": "worker"})
    store = client.app.state.store

    async def consume() -> None:
        async for _ in _stream_chat_sse(
            _FakeStreamGraph(), "s1", "main-agent", "委派", ChatState(), store=store
        ):
            pass

    asyncio.run(consume())

    records = store.list_delegations("worker")
    assert len(records) == 1
    assert records[0]["parent_agent_id"] == "main-agent"
    assert records[0]["task_description"] == "计算报表"


# ---------------------------------------------------------------------------
# 会话记录完整性：stream 结束后持久化完整 assistant 消息
# (答复 / 推理 / tool_calls / todo / 委派 / 审批)
# ---------------------------------------------------------------------------


class _FakeInterruptGraph(_FakeStreamGraph):
    """astream that also fires a HITL interrupt via stream_mode="values"""

    async def astream(self, input_data, config=None, stream_mode=None):
        async for chunk in super().astream(input_data, config, stream_mode):
            yield chunk
        # Last values snapshot carries an interrupt (approval request).
        from types import SimpleNamespace

        interrupt = SimpleNamespace(
            value={
                "action_requests": [
                    {
                        "name": "delete",
                        "args": {"file_path": "/tmp/x.txt"},
                        "description": "Delete file",
                    }
                ]
            }
        )
        yield ("values", {"__interrupt__": [interrupt]})


class _FakeReasoningStreamGraph(_FakeInterruptGraph):
    """astream whose message chunks carry reasoning_content, plus a HITL
    interrupt — covers both reasoning and approval persistence."""

    async def astream(self, input_data, config=None, stream_mode=None):
        from langchain_core.messages import AIMessageChunk

        async for chunk in super().astream(input_data, config, stream_mode):
            if (
                isinstance(chunk, (list, tuple))
                and chunk[0] == "messages"
                and hasattr(chunk[1][0], "content")
            ):
                # Emit a reasoning token before the reply token.
                reasoning = AIMessageChunk(
                    content="", additional_kwargs={"reasoning_content": "先分析需求"}
                )
                yield ("messages", (reasoning, {}))
            yield chunk


def _make_session(store, session_id: str, agent_id: str) -> None:
    """Seed a session record so _append_message can persist into it."""
    from datetime import datetime, timezone

    store.save_session(session_id, {
        "session_id": session_id,
        "agent_id": agent_id,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        "messages": [],
    })


def test_stream_persists_full_assistant_turn(client) -> None:
    """流结束后，会话记录包含完整 assistant 消息（答复/tool_calls/todo/委派）。"""
    import asyncio

    from agentcore.runtime.chat_router import ChatState, _stream_chat_sse

    client.post("/api/agents", json={"agent_id": "worker"})
    store = client.app.state.store
    _make_session(store, "persist-s1", "main-agent")
    from agentcore.runtime.chat_router import _append_message

    _append_message(store, "persist-s1", "user", "委派任务")

    async def consume() -> None:
        async for _ in _stream_chat_sse(
            _FakeStreamGraph(), "persist-s1", "main-agent", "委派任务",
            ChatState(), store=store,
        ):
            pass

    asyncio.run(consume())

    session = store.load_session("persist-s1")
    assert session is not None
    messages = session["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]

    last = messages[-1]
    assert last["content"] == "最终回复"
    assert {tc["name"] for tc in last["tool_calls"]} == {"task", "write_todos"}
    # Todo plan captured from the last write_todos call.
    assert [t["title"] for t in last["todos"]] == ["拆解需求", "实施"]
    # Delegation captured.
    assert last["delegations"] == [
        {"subagent": "worker", "description": "计算报表"}
    ]


def test_stream_persists_reasoning_and_approval_request(client) -> None:
    """推理过程与审批请求也会写入会话记录。"""
    import asyncio

    from agentcore.runtime.chat_router import ChatState, _stream_chat_sse

    client.post("/api/agents", json={"agent_id": "worker"})
    store = client.app.state.store
    _make_session(store, "persist-s2", "main-agent")

    async def consume() -> None:
        async for _ in _stream_chat_sse(
            _FakeReasoningStreamGraph(), "persist-s2", "main-agent", "删除文件",
            ChatState(), store=store,
        ):
            pass

    asyncio.run(consume())

    session = store.load_session("persist-s2")
    assert session is not None
    last = session["messages"][-1]
    assert last["reasoning"] == "先分析需求"
    assert last["approval_request"]["actions"][0]["name"] == "delete"


def test_mark_last_pending_approval_records_decision(client) -> None:
    """审批决策回写到发起审批的 assistant 消息。"""
    from agentcore.runtime.chat_router import _mark_last_pending_approval

    store = client.app.state.store
    _make_session(store, "approve-s1", "main-agent")
    from agentcore.runtime.chat_router import _append_message as _append

    _append(
        store, "approve-s1", "assistant", "",
        extras={
            "approval_request": {
                "actions": [{"name": "delete", "args": {}, "description": ""}]
            }
        },
    )
    _append(store, "approve-s1", "assistant", "最终答复")

    _mark_last_pending_approval(store, "approve-s1", "approve", "delete")

    session = store.load_session("approve-s1")
    assert session is not None
    first = session["messages"][0]
    assert first["approval"] == {
        "decision": "approve",
        "tool_name": "delete",
        "timestamp": first["approval"]["timestamp"],
    }
    # The following plain assistant message is untouched.
    assert "approval" not in session["messages"][1]


def test_mark_last_pending_approval_resolves_tool_name_from_request() -> None:
    """tool_name 探测为空时，从持久化的审批请求推断工具名（两轮审批）。"""
    from agentcore.runtime.chat_router import (
        _append_message,
        _mark_last_pending_approval,
    )

    class _FakeStore:
        def __init__(self) -> None:
            from datetime import datetime, timezone

            self.session = {
                "session_id": "s",
                "agent_id": "a",
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "updated_at": datetime.now(tz=timezone.utc).isoformat(),
                "messages": [],
            }

        def load_session(self, session_id):
            return self.session

        def save_session(self, session_id, data):
            self.session = data

    store = _FakeStore()
    _append_message(store, "s", "assistant", "", extras={
        "approval_request": {
            "actions": [{"name": "write_file", "args": {}, "description": ""}]
        }
    })

    # Round 1: probe empty → tool name inferred from the current request.
    _mark_last_pending_approval(
        store, "s", "approve", "",
        new_approval_request={
            "actions": [{"name": "delete", "args": {}, "description": ""}]
        },
    )
    msg = store.session["messages"][0]
    assert msg["approval"]["tool_name"] == "write_file"
    assert msg["approval_request"]["actions"][0]["name"] == "delete"

    # Round 2: approve the delete — tool name follows the updated request.
    _mark_last_pending_approval(store, "s", "approve", "")
    assert store.session["messages"][0]["approval"]["tool_name"] == "delete"


def test_mark_last_pending_approval_clears_request_when_finished() -> None:
    """审批链完成后清除陈旧的 pending 请求，避免历史误显示可操作卡片。"""
    from agentcore.runtime.chat_router import (
        _append_message,
        _mark_last_pending_approval,
    )

    class _FakeStore:
        def __init__(self) -> None:
            from datetime import datetime, timezone

            self.session = {
                "session_id": "s",
                "agent_id": "a",
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "updated_at": datetime.now(tz=timezone.utc).isoformat(),
                "messages": [],
            }

        def load_session(self, session_id):
            return self.session

        def save_session(self, session_id, data):
            self.session = data

    store = _FakeStore()
    _append_message(store, "s", "assistant", "", extras={
        "approval_request": {
            "actions": [{"name": "delete", "args": {}, "description": ""}]
        }
    })

    _mark_last_pending_approval(store, "s", "approve", "delete",
                                clear_request=True)
    msg = store.session["messages"][0]
    assert msg["approval"]["decision"] == "approve"
    assert "approval_request" not in msg


def test_collect_turn_metadata_extracts_plan_and_delegation() -> None:
    """同步 chat 的元数据提取：reasoning + write_todos + task 委派。"""
    from agentcore.runtime.chat_router import _collect_turn_metadata

    result = {
        "messages": [
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "推理中"},
                tool_calls=[
                    {
                        "name": "write_todos",
                        "args": {"todos": [{"title": "A", "status": "pending"}]},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"subagent_type": "calc", "description": "算一下"},
                        "id": "c2",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="结果"),
        ]
    }
    meta = _collect_turn_metadata(result)
    assert meta["reasoning"] == "推理中"
    assert meta["todos"] == [{"title": "A", "status": "pending"}]
    assert meta["delegations"] == [
        {"subagent": "calc", "description": "算一下"}
    ]
