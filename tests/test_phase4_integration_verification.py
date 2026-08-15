"""Phase 4: Cross-phase integration tests and spec verification (Tasks 4.1, 4.2).

Covers:
- Task 4.1: Spec requirement verification across all 7 spec areas.
- Task 4.2: Cross-phase integration — HITL + subagents + streaming.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentcore.runtime import agent_factory as agent_factory_mod
from agentcore.runtime import chat_router
from agentcore.runtime.agent_factory import AgentFactory, _ToolExclusionMiddleware
from agentcore.runtime.subagent_registry import SubAgentRegistry


# ---------------------------------------------------------------------------
# Task 4.1 — Spec verification: runtime-security-hardening
# ---------------------------------------------------------------------------


class TestSpecRuntimeSecurityHardening:
    """Verify runtime-security-hardening spec requirements."""

    def test_hitl_interrupt_detection(self):
        """HITL: interrupt detection in agent results (Scenario: dangerous operation paused)."""
        fake_interrupt = SimpleNamespace(
            value={
                "action_requests": [
                    {"name": "execute", "args": {"cmd": "rm -rf /"}, "description": "Dangerous"},
                ],
                "review_configs": [],
            },
            id="hitl-1",
        )
        result = {"__interrupt__": [fake_interrupt], "messages": []}
        interrupts = chat_router._extract_interrupts_from_result(result)
        assert len(interrupts) == 1
        info = chat_router._build_approval_request_info(interrupts)
        assert info["actions"][0]["name"] == "execute"

    def test_audit_record_created_on_decision(self, tmp_path, monkeypatch):
        """HITL: decision is auditable (Scenario: decision auditable)."""
        monkeypatch.setenv("AGENTCORE_DATA_DIR", str(tmp_path))
        from agentcore.runtime.audit import record_approval_decision, load_audit_records

        record_approval_decision(
            agent_id="a", session_id="s", decision="approve",
            tool_name="write_file", final_args={"path": "/tmp"}, operator="user",
        )
        records = load_audit_records()
        assert len(records) == 1
        assert records[0]["decision"] == "approve"
        assert "ts" in records[0]

    def test_sandbox_unavailable_raises_explicitly(self):
        """Sandbox: unavailable sandbox raises explicit error (Scenario: sandbox unavailable)."""
        from agentcore.runtime.sandbox import SandboxUnavailableError
        from agentcore.runtime.agent_factory import _create_backend

        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("E2B_API_KEY", None)
            with pytest.raises(SandboxUnavailableError, match="E2B_API_KEY"):
                _create_backend({"backend": {"type": "sandbox"}}, workspace_dir=None)


# ---------------------------------------------------------------------------
# Task 4.1 — Spec verification: multiagent-collaboration
# ---------------------------------------------------------------------------


class TestSpecMultiagentCollaboration:
    """Verify multiagent-collaboration spec requirements."""

    def test_subagent_specialist_config(self, tmp_path):
        """SubAgent retains specialist config (model/permissions/interrupt_on)."""
        ws_dir = tmp_path / "specialist"
        ws_dir.mkdir()
        ws = MagicMock()
        ws.workspace_dir = ws_dir
        ws.get_system_prompt.return_value = "Expert prompt"
        ws.read_agent_config.return_value = {
            "model": {"provider": "anthropic", "name": "claude-3"},
            "tools": {"enabled": ["read_file"], "disabled": []},
            "settings": {
                "description": "Code reviewer specialist",
                "permissions": [{"operations": ["read"], "paths": ["/src/*"], "mode": "allow"}],
                "interrupt_rules": [{"tool_name": "write_file", "require_approval": True}],
            },
        }

        manager = MagicMock()
        manager._workspaces = {"specialist": ws}
        specs = SubAgentRegistry(manager).get_subagents()

        assert len(specs) == 1
        spec = specs[0]
        assert spec["model"] == "anthropic:claude-3"
        assert "permissions" in spec
        assert spec["interrupt_on"] == {"write_file": True}

    def test_dynamic_description_reflects_capability(self):
        """Description reflects agent capability, not just identifier."""
        ws = MagicMock()
        ws.workspace_dir = MagicMock()
        ws.get_system_prompt.return_value = "prompt"
        ws.read_agent_config.return_value = {
            "model": {},
            "tools": {"enabled": ["read_file", "grep"], "disabled": []},
            "settings": {"description": "File analysis expert"},
        }

        manager = MagicMock()
        manager._workspaces = {"analyzer": ws}
        specs = SubAgentRegistry(manager).get_subagents()

        desc = specs[0]["description"]
        assert "File analysis expert" in desc
        assert "read_file" in desc

    def test_self_exclusion_prevents_recursion(self):
        """Self-exclusion prevents infinite recursive delegation."""
        ws_a = MagicMock()
        ws_a.workspace_dir = MagicMock()
        ws_a.get_system_prompt.return_value = "A"
        ws_a.read_agent_config.return_value = {"model": {}, "tools": {}, "settings": {}}

        manager = MagicMock()
        manager._workspaces = {"a": ws_a}
        specs = SubAgentRegistry(manager).get_subagents(exclude_agent_id="a")
        assert len(specs) == 0  # "a" excluded itself

    def test_any_agent_can_delegate_with_flag(self, tmp_path):
        """Any agent with enable_subagents=true can delegate (multi-way)."""
        ws_main = MagicMock()
        ws_main.workspace_dir = tmp_path / "main"
        ws_main.workspace_dir.mkdir(exist_ok=True)
        ws_main.get_system_prompt.return_value = "Main"
        ws_main.read_agent_config.return_value = {
            "model": {}, "tools": {},
            "settings": {"enable_subagents": True},
        }
        ws_helper = MagicMock()
        ws_helper.workspace_dir = tmp_path / "helper"
        ws_helper.workspace_dir.mkdir(exist_ok=True)
        ws_helper.get_system_prompt.return_value = "Helper"
        ws_helper.read_agent_config.return_value = {
            "model": {}, "tools": {}, "settings": {},
        }

        manager = MagicMock()
        manager._workspaces = {"main": ws_main, "helper": ws_helper}

        # Main agent can see helper.
        specs = SubAgentRegistry(manager).get_subagents(exclude_agent_id="main")
        assert len(specs) == 1
        assert specs[0]["name"] == "helper"


# ---------------------------------------------------------------------------
# Task 4.1 — Spec verification: streaming-observability
# ---------------------------------------------------------------------------


class TestSpecStreamingObservability:
    """Verify streaming-observability spec requirements."""

    def test_streaming_messages_projection(self):
        """Streaming: messages are pushed incrementally."""
        from agentcore.runtime.chat_router import _stream_chat_sse, ChatState

        mock_graph = MagicMock()

        async def mock_astream(*args, **kwargs):
            yield ("messages", (SimpleNamespace(content="Token 1"), {}))
            yield ("messages", (SimpleNamespace(content=" Token 2"), {}))
            yield ("values", {})

        mock_graph.astream = mock_astream
        chat_state = ChatState()

        async def _collect():
            return [e for e in await _collect_events(mock_graph, chat_state)]

        async def _collect_events(graph, cs):
            events = []
            async for event in _stream_chat_sse(graph, "s1", "a1", "Hi", cs):
                events.append(event)
            return events

        events = asyncio.run(_collect_events(mock_graph, chat_state))
        msg_events = [e for e in events if "event: messages" in e]
        assert len(msg_events) >= 2  # At least 2 message events

    def test_streaming_interrupt_projection(self):
        """Streaming: interrupts are visible in the stream."""
        from agentcore.runtime.chat_router import _stream_chat_sse, ChatState

        mock_graph = MagicMock()
        fake_interrupt = SimpleNamespace(
            value={"action_requests": [{"name": "delete", "args": {}, "description": "Del"}], "review_configs": []},
            id="i1",
        )

        async def mock_astream(*args, **kwargs):
            yield ("values", {"__interrupt__": [fake_interrupt]})

        mock_graph.astream = mock_astream

        async def _run():
            events = []
            async for e in _stream_chat_sse(mock_graph, "s1", "a1", "Del", ChatState()):
                events.append(e)
            return events

        events = asyncio.run(_run())
        interrupt_events = [e for e in events if "event: interrupts" in e]
        assert len(interrupt_events) == 1

    def test_streaming_subagent_projection(self):
        """Streaming: subagent delegation is visible."""
        from agentcore.runtime.chat_router import _stream_chat_sse, ChatState

        mock_graph = MagicMock()

        async def mock_astream(*args, **kwargs):
            yield ("updates", {"task": {"result": "subagent done"}})
            yield ("values", {})

        mock_graph.astream = mock_astream

        async def _run():
            events = []
            async for e in _stream_chat_sse(mock_graph, "s1", "a1", "Work", ChatState()):
                events.append(e)
            return events

        events = asyncio.run(_run())
        sub_events = [e for e in events if "event: subagents" in e]
        assert len(sub_events) >= 1


# ---------------------------------------------------------------------------
# Task 4.1 — Spec verification: task-planning
# ---------------------------------------------------------------------------


class TestSpecTaskPlanning:
    """Verify task-planning spec requirements."""

    def test_planning_disabled_no_write_todos(self):
        """Planning disabled: no write_todos tool exposed."""
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

    def test_planning_enabled_has_write_todos(self):
        """Planning enabled: write_todos tool is available."""
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
# Task 4.1 — Spec verification: middleware-extensibility
# ---------------------------------------------------------------------------


class TestSpecMiddlewareExtensibility:
    """Verify middleware-extensibility spec requirements."""

    def test_tool_exclusion_uses_public_middleware(self):
        """Tool exclusion uses public middleware, not private SDK imports."""
        import sys
        mod = sys.modules.get("agentcore.runtime.agent_factory")
        assert mod is not None
        source_file = getattr(mod, "__file__", "")
        with open(source_file, encoding="utf-8") as f:
            source = f.read()
        assert "_tool_exclusion" not in source

    def test_tool_exclusion_middleware_exists(self):
        """_ToolExclusionMiddleware is defined in agent_factory (public)."""
        assert hasattr(agent_factory_mod, "_ToolExclusionMiddleware")


# ---------------------------------------------------------------------------
# Task 4.2 — Cross-phase integration: HITL + subagents + streaming
# ---------------------------------------------------------------------------


class TestCrossPhaseIntegration:
    """Verify cross-phase interaction: HITL approval + subagents + streaming."""

    def test_subagent_with_interrupt_rules(self, tmp_path):
        """Integration: subagent carries interrupt_on for HITL."""
        ws_dir = tmp_path / "safe_agent"
        ws_dir.mkdir()
        ws = MagicMock()
        ws.workspace_dir = ws_dir
        ws.get_system_prompt.return_value = "Safe agent"
        ws.read_agent_config.return_value = {
            "model": {"provider": "openai", "name": "gpt-4o-mini"},
            "tools": {},
            "settings": {
                "description": "Safe code executor",
                "interrupt_rules": [
                    {"tool_name": "execute", "require_approval": True},
                ],
            },
        }

        manager = MagicMock()
        manager._workspaces = {"safe_agent": ws}
        specs = SubAgentRegistry(manager).get_subagents()

        spec = specs[0]
        assert spec["interrupt_on"] == {"execute": True}
        assert "HITL required for: execute" in spec["description"]

    def test_streaming_with_subagent_and_interrupt(self):
        """Integration: streaming shows both subagent activity and interrupts."""
        from agentcore.runtime.chat_router import _stream_chat_sse, ChatState

        mock_graph = MagicMock()
        fake_interrupt = SimpleNamespace(
            value={
                "action_requests": [{"name": "write_file", "args": {"path": "/etc"}, "description": "Write"}],
                "review_configs": [],
            },
            id="int-1",
        )

        async def mock_astream(*args, **kwargs):
            # First: subagent delegation.
            yield ("updates", {"task": {"delegation": "helper started"}})
            # Then: interrupt for approval.
            yield ("values", {"__interrupt__": [fake_interrupt]})

        mock_graph.astream = mock_astream

        async def _run():
            events = []
            async for e in _stream_chat_sse(mock_graph, "s1", "a1", "Work", ChatState()):
                events.append(e)
            return events

        events = asyncio.run(_run())

        # Both subagent and interrupt events must be present.
        sub_events = [e for e in events if "event: subagents" in e]
        int_events = [e for e in events if "event: interrupts" in e]
        assert len(sub_events) >= 1
        assert len(int_events) >= 1

        # Interrupt must contain approval info.
        int_data = json.loads(int_events[0].split("data: ")[1].strip())
        assert int_data["status"] == "pending_approval"
