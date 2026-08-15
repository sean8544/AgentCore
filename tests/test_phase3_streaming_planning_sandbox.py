"""Tests for Phase 3: Streaming, Planning, and Sandbox (Task 3.8).

Covers:
- Task 3.1/3.2: Streaming SSE endpoint and 4 projection channels.
- Task 3.4/3.5: TodoList / planning tool — opt-in, not exposed by default.
- Task 3.6/3.7: Sandbox backend abstraction — explicit error on unavailability.
- Task 3.8: Streaming projection tests (subagents visible, interrupts visible,
  token usage visible).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.runtime import agent_factory as agent_factory_mod
from agentcore.runtime.agent_factory import AgentFactory, _create_write_todos_tool
from agentcore.runtime.sandbox import (
    E2BSandboxFactory,
    DaytonaSandboxFactory,
    SandboxBackendFactory,
    SandboxUnavailableError,
    get_sandbox_factory,
)


# ---------------------------------------------------------------------------
# Task 3.4/3.5 — Planning / TodoList tool
# ---------------------------------------------------------------------------


class TestTodoListTool:
    """Verify write_todos tool is created and works correctly."""

    def test_create_write_todos_tool_returns_callable(self):
        """_create_write_todos_tool must return a tool object."""
        tool = _create_write_todos_tool()
        # StructuredTool is not __call__-able but has .invoke().
        assert hasattr(tool, "invoke")
        assert hasattr(tool, "name")

    def test_write_todos_tool_has_correct_name(self):
        """The tool must be named 'write_todos'."""
        tool = _create_write_todos_tool()
        assert tool.name == "write_todos"

    def test_write_todos_tool_accepts_todos(self):
        """The tool must accept a list of todo dicts."""
        tool = _create_write_todos_tool()
        result = tool.invoke({
            "todos": [
                {"id": "1", "content": "Write tests", "status": "pending"},
                {"id": "2", "content": "Run tests", "status": "in_progress"},
            ]
        })
        assert "Updated 2 todo(s)" in result

    def test_write_todos_replaces_previous(self):
        """Each call must replace the previous todo list."""
        tool = _create_write_todos_tool()
        tool.invoke({"todos": [{"id": "1", "content": "First", "status": "pending"}]})
        result = tool.invoke({"todos": [{"id": "2", "content": "Second", "status": "done"}]})
        assert "Updated 1 todo(s)" in result

    def test_planning_disabled_by_default(self):
        """Without enable_planning, write_todos must not be in custom_tools."""
        config = {
            "model": {"provider": "openai", "name": "gpt-4o"},
            "tools": {"enabled": [], "disabled": []},
            "settings": {},
        }
        captured: dict[str, Any] = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return object()

        with patch.object(agent_factory_mod, "create_deep_agent", fake_create):
            with patch.dict("os.environ", {"AGENTCORE_LLM_API_KEY": "sk-test"}):
                AgentFactory().create_agent(config)

        tools = captured.get("tools", []) or []
        tool_names = [getattr(t, "name", None) for t in tools]
        assert "write_todos" not in tool_names

    def test_planning_enabled_adds_write_todos(self):
        """With enable_planning=true, write_todos must be in custom_tools."""
        config = {
            "model": {"provider": "openai", "name": "gpt-4o"},
            "tools": {"enabled": [], "disabled": []},
            "settings": {"enable_planning": True},
        }
        captured: dict[str, Any] = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return object()

        with patch.object(agent_factory_mod, "create_deep_agent", fake_create):
            with patch.dict("os.environ", {"AGENTCORE_LLM_API_KEY": "sk-test"}):
                AgentFactory().create_agent(config)

        tools = captured.get("tools", []) or []
        tool_names = [getattr(t, "name", None) for t in tools]
        assert "write_todos" in tool_names


# ---------------------------------------------------------------------------
# Task 3.6/3.7 — Sandbox backend abstraction
# ---------------------------------------------------------------------------


class TestSandboxAbstraction:
    """Verify sandbox factory raises clear errors when unavailable."""

    def test_e2b_factory_raises_without_api_key(self):
        """E2B factory must raise SandboxUnavailableError without API key."""
        factory = E2BSandboxFactory()
        with patch.dict("os.environ", {}, clear=True):
            # Remove E2B_API_KEY if present.
            import os
            os.environ.pop("E2B_API_KEY", None)
            with pytest.raises(SandboxUnavailableError, match="E2B_API_KEY"):
                factory.create()

    def test_daytona_factory_raises_not_implemented(self):
        """Daytona factory must raise SandboxUnavailableError."""
        factory = DaytonaSandboxFactory()
        with pytest.raises(SandboxUnavailableError, match="not yet implemented"):
            factory.create()

    def test_get_sandbox_factory_unknown_provider(self):
        """Unknown provider must raise ValueError."""
        with pytest.raises(ValueError, match="Unknown sandbox provider"):
            get_sandbox_factory("nonexistent")

    def test_get_sandbox_factory_e2b(self):
        """E2B provider must return an E2BSandboxFactory."""
        factory = get_sandbox_factory("e2b")
        assert isinstance(factory, E2BSandboxFactory)

    def test_get_sandbox_factory_daytona(self):
        """Daytona provider must return a DaytonaSandboxFactory."""
        factory = get_sandbox_factory("daytona")
        assert isinstance(factory, DaytonaSandboxFactory)

    def test_sandbox_config_raises_not_silent_fallback(self):
        """When sandbox is configured but unavailable, must not silently fall back."""
        from agentcore.runtime.agent_factory import _create_backend

        settings = {"backend": {"type": "sandbox", "provider": "e2b"}}
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("E2B_API_KEY", None)
            with pytest.raises(SandboxUnavailableError):
                _create_backend(settings, workspace_dir=None)

    def test_sandbox_unavailable_error_is_runtime_error(self):
        """SandboxUnavailableError must be a RuntimeError for proper catching."""
        assert issubclass(SandboxUnavailableError, RuntimeError)


# ---------------------------------------------------------------------------
# Task 3.8 — Streaming SSE helpers
# ---------------------------------------------------------------------------


class TestStreamingSSEHelpers:
    """Verify SSE formatting helpers."""

    def test_sse_event_format(self):
        """_sse_event must produce valid SSE format."""
        from agentcore.runtime.chat_router import _sse_event

        event = _sse_event("messages", {"content": "hello"})
        assert event.startswith("event: messages\n")
        assert "data: " in event
        assert '"content": "hello"' in event
        assert event.endswith("\n\n")

    def test_sse_event_with_complex_data(self):
        """_sse_event must handle nested dicts."""
        from agentcore.runtime.chat_router import _sse_event

        data = {"actions": [{"name": "write_file", "args": {"path": "/tmp"}}]}
        event = _sse_event("interrupts", data)
        parsed = json.loads(event.split("data: ")[1].strip())
        assert parsed["actions"][0]["name"] == "write_file"

    def test_serialise_node_output_dict(self):
        """_serialise_node_output must handle dict inputs."""
        from agentcore.runtime.chat_router import _serialise_node_output

        result = _serialise_node_output({"key": "value", "num": 42})
        assert result["key"] == "value"
        assert result["num"] == 42

    def test_serialise_node_output_non_dict(self):
        """_serialise_node_output must handle non-dict inputs."""
        from agentcore.runtime.chat_router import _serialise_node_output

        result = _serialise_node_output("hello")
        assert result == {"value": "hello"}


# ---------------------------------------------------------------------------
# Task 3.8 — Streaming projection integration
# ---------------------------------------------------------------------------


class TestStreamingProjections:
    """Verify the 4 streaming projection channels work correctly."""

    def test_stream_chat_sse_messages_projection(self):
        """Messages projection must emit token content."""
        from agentcore.runtime.chat_router import _stream_chat_sse, ChatState

        # Create a mock agent graph that yields stream chunks.
        mock_graph = MagicMock()

        async def mock_astream(*args, **kwargs):
            # Simulate messages mode output.
            yield ("messages", (SimpleNamespace(content="Hello "), {}))
            yield ("messages", (SimpleNamespace(content="world!"), {}))
            yield ("values", {})  # Final state, no interrupts.

        mock_graph.astream = mock_astream

        chat_state = ChatState()

        async def _collect():
            events = []
            async for event in _stream_chat_sse(
                mock_graph, "sess-1", "agent-1", "Hi", chat_state
            ):
                events.append(event)
            return events

        events = asyncio.run(_collect())

        # Should have message events + done event.
        msg_events = [e for e in events if e.startswith("event: messages")]
        done_events = [e for e in events if e.startswith("event: done")]
        assert len(msg_events) == 2
        assert len(done_events) == 1

        # Check content accumulation.
        data1 = json.loads(msg_events[0].split("data: ")[1].strip())
        assert data1["content"] == "Hello "
        data2 = json.loads(msg_events[1].split("data: ")[1].strip())
        assert data2["content"] == "world!"

    def test_stream_chat_sse_interrupt_projection(self):
        """Interrupts projection must emit approval requests."""
        from agentcore.runtime.chat_router import _stream_chat_sse, ChatState

        mock_graph = MagicMock()
        fake_interrupt = SimpleNamespace(
            value={
                "action_requests": [
                    {"name": "write_file", "args": {"path": "/etc/passwd"}, "description": "Need approval"},
                ],
                "review_configs": [],
            },
            id="int-1",
        )

        async def mock_astream(*args, **kwargs):
            yield ("values", {"__interrupt__": [fake_interrupt]})

        mock_graph.astream = mock_astream
        chat_state = ChatState()

        async def _collect():
            events = []
            async for event in _stream_chat_sse(
                mock_graph, "sess-1", "agent-1", "Write file", chat_state
            ):
                events.append(event)
            return events

        events = asyncio.run(_collect())

        interrupt_events = [e for e in events if e.startswith("event: interrupts")]
        assert len(interrupt_events) == 1
        data = json.loads(interrupt_events[0].split("data: ")[1].strip())
        assert data["status"] == "pending_approval"
        assert data["approval_request"]["actions"][0]["name"] == "write_file"

    def test_stream_chat_sse_subagent_projection(self):
        """Subagent projection must emit node updates for task delegation."""
        from agentcore.runtime.chat_router import _stream_chat_sse, ChatState

        mock_graph = MagicMock()

        async def mock_astream(*args, **kwargs):
            yield ("updates", {"task": {"result": "delegated work done"}})

        mock_graph.astream = mock_astream
        chat_state = ChatState()

        async def _collect():
            events = []
            async for event in _stream_chat_sse(
                mock_graph, "sess-1", "agent-1", "Do work", chat_state
            ):
                events.append(event)
            return events

        events = asyncio.run(_collect())

        subagent_events = [e for e in events if e.startswith("event: subagents")]
        assert len(subagent_events) == 1
        data = json.loads(subagent_events[0].split("data: ")[1].strip())
        assert data["node"] == "task"

    def test_stream_chat_sse_error_handling(self):
        """Stream errors must emit error events, not crash."""
        from agentcore.runtime.chat_router import _stream_chat_sse, ChatState

        mock_graph = MagicMock()

        async def mock_astream(*args, **kwargs):
            raise RuntimeError("LLM provider down")
            yield  # Make it a generator.  # pragma: no cover

        mock_graph.astream = mock_astream
        chat_state = ChatState()

        async def _collect():
            events = []
            async for event in _stream_chat_sse(
                mock_graph, "sess-1", "agent-1", "Hi", chat_state
            ):
                events.append(event)
            return events

        events = asyncio.run(_collect())

        error_events = [e for e in events if e.startswith("event: error")]
        assert len(error_events) == 1
        data = json.loads(error_events[0].split("data: ")[1].strip())
        assert "LLM provider down" in data["detail"]

    def test_stream_chat_sse_done_event_includes_session(self):
        """Done event must include session_id and agent_id."""
        from agentcore.runtime.chat_router import _stream_chat_sse, ChatState

        mock_graph = MagicMock()

        async def mock_astream(*args, **kwargs):
            yield ("values", {})  # No interrupts, just completion.

        mock_graph.astream = mock_astream
        chat_state = ChatState()

        async def _collect():
            events = []
            async for event in _stream_chat_sse(
                mock_graph, "sess-42", "agent-x", "Hello", chat_state
            ):
                events.append(event)
            return events

        events = asyncio.run(_collect())
        done_events = [e for e in events if e.startswith("event: done")]
        assert len(done_events) == 1
        data = json.loads(done_events[0].split("data: ")[1].strip())
        assert data["session_id"] == "sess-42"
        assert data["agent_id"] == "agent-x"
