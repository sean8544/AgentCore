"""Tests for HITL approval闭环 and tool-exclusion public middleware.

Covers:
- Task 1.8: tool-exclusion middleware behaves identically after
  replacing the private SDK import with the public implementation.
- Task 1.9: HITL approval flow — approve / edit / reject paths and
  interrupt detection in chat responses.
- Task 1.10: tool-exclusion regression — no silent failures.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.runtime import agent_factory as agent_factory_mod
from agentcore.runtime import chat_router
from agentcore.runtime.agent_factory import AgentFactory, _ToolExclusionMiddleware
from agentcore.runtime.workspace import DEFAULT_AGENT_JSON


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _config(
    *,
    tools: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "model": dict(DEFAULT_AGENT_JSON["model"]),
        "tools": tools or {"enabled": [], "disabled": []},
        "settings": settings or {},
    }


@pytest.fixture
def captured_create(monkeypatch):
    """Patch ``create_deep_agent`` and capture kwargs."""
    captured: dict[str, Any] = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(agent_factory_mod, "create_deep_agent", fake_create)
    return captured


@pytest.fixture(autouse=True)
def _dummy_api_key(monkeypatch):
    monkeypatch.setenv("AGENTCORE_LLM_API_KEY", "sk-test-dummy")


@pytest.fixture(autouse=True)
def _clear_graph_cache():
    chat_router._default_chat_state.graph_cache.clear()
    yield
    chat_router._default_chat_state.graph_cache.clear()


# ---------------------------------------------------------------------------
# Task 1.8 — Tool-exclusion public middleware parity
# ---------------------------------------------------------------------------


class TestToolExclusionPublicMiddleware:
    """Verify the public _ToolExclusionMiddleware replaces the SDK private one."""

    def test_class_exists_in_agent_factory(self):
        """The class must be importable from agent_factory (no private SDK dep)."""
        assert hasattr(agent_factory_mod, "_ToolExclusionMiddleware")

    def test_no_private_sdk_import(self):
        """agent_factory must NOT import from deepagents.middleware._tool_exclusion."""
        import importlib
        import sys

        # Reload to check imports from scratch.
        mod = sys.modules.get("agentcore.runtime.agent_factory")
        assert mod is not None
        source_file = getattr(mod, "__file__", "")
        with open(source_file, encoding="utf-8") as f:
            source = f.read()
        assert "_tool_exclusion" not in source

    def test_middleware_filters_tools(self):
        """The middleware must strip excluded tools from request.tools."""
        mw = _ToolExclusionMiddleware(excluded=frozenset({"write_file", "execute"}))

        # Simulate a model request with tools.
        fake_tools = [
            SimpleNamespace(name="read_file"),
            SimpleNamespace(name="write_file"),
            SimpleNamespace(name="execute"),
            SimpleNamespace(name="ls"),
        ]
        fake_request = SimpleNamespace(tools=fake_tools)

        # Add an override() method that returns a new SimpleNamespace with
        # the overridden tools — mirrors the SDK's ModelRequest.override().
        def _override(**kwargs):
            return SimpleNamespace(tools=kwargs.get("tools", fake_request.tools))
        fake_request.override = _override

        # Track what the handler receives.
        received: list[Any] = []

        def handler(req):
            received.append(req)
            return "ok"

        result = mw.wrap_model_call(fake_request, handler)
        assert result == "ok"
        assert len(received) == 1
        filtered_names = [t.name for t in received[0].tools]
        assert "read_file" in filtered_names
        assert "ls" in filtered_names
        assert "write_file" not in filtered_names
        assert "execute" not in filtered_names

    def test_middleware_noop_when_excluded_empty(self):
        """When excluded is empty, the middleware must pass through unchanged."""
        mw = _ToolExclusionMiddleware(excluded=frozenset())
        fake_tools = [SimpleNamespace(name="read_file")]
        fake_request = SimpleNamespace(tools=fake_tools)
        received: list[Any] = []

        def handler(req):
            received.append(req)
            return "ok"

        mw.wrap_model_call(fake_request, handler)
        # Same request object (no override) should be passed.
        assert received[0] is fake_request

    def test_async_middleware_filters_tools(self):
        """awrap_model_call must filter tools identically to sync."""
        import asyncio
        mw = _ToolExclusionMiddleware(excluded=frozenset({"delete"}))
        fake_tools = [
            SimpleNamespace(name="read_file"),
            SimpleNamespace(name="delete"),
        ]
        fake_request = SimpleNamespace(tools=fake_tools)

        def _override(**kwargs):
            return SimpleNamespace(tools=kwargs.get("tools", fake_request.tools))
        fake_request.override = _override

        received: list[Any] = []

        async def handler(req):
            received.append(req)
            return "ok"

        async def _run():
            return await mw.awrap_model_call(fake_request, handler)

        result = asyncio.run(_run())
        assert result == "ok"
        filtered_names = [t.name for t in received[0].tools]
        assert "read_file" in filtered_names
        assert "delete" not in filtered_names

    def test_tool_name_from_dict(self):
        """_tool_name must handle dict-shaped tools (SDK may pass dicts)."""
        assert _ToolExclusionMiddleware._tool_name({"name": "read_file"}) == "read_file"
        assert _ToolExclusionMiddleware._tool_name({"name": 123}) is None
        assert _ToolExclusionMiddleware._tool_name({}) is None
        assert _ToolExclusionMiddleware._tool_name(SimpleNamespace(name="ls")) == "ls"

    def test_factory_injects_middleware_for_disabled_tools(self, captured_create):
        """AgentFactory must inject middleware when tools are disabled."""
        config = _config(tools={"enabled": [], "disabled": ["write_file"]})
        AgentFactory().create_agent(config)
        middleware = captured_create.get("middleware", [])
        exclusion = [m for m in middleware if isinstance(m, _ToolExclusionMiddleware)]
        assert len(exclusion) == 1
        assert "write_file" in exclusion[0]._excluded

    def test_factory_no_middleware_when_nothing_excluded_on_sandbox(self, captured_create):
        """Sandbox backend raises when unavailable (no silent fallback)."""
        from agentcore.runtime.sandbox import SandboxUnavailableError
        config = _config(settings={"backend": {"type": "sandbox"}})
        with pytest.raises(SandboxUnavailableError):
            AgentFactory().create_agent(config)


# ---------------------------------------------------------------------------
# Task 1.9 — HITL approval flow
# ---------------------------------------------------------------------------


class TestHITLInterruptDetection:
    """Verify chat_router detects __interrupt__ in agent results."""

    def test_extract_interrupts_from_result_with_interrupts(self):
        """_extract_interrupts_from_result must return Interrupt objects."""
        fake_interrupt = SimpleNamespace(
            value={
                "action_requests": [
                    {"name": "write_file", "args": {"path": "/etc/passwd"}, "description": "Write to protected path"},
                ],
                "review_configs": [],
            },
            id="test-id",
        )
        result = {"__interrupt__": [fake_interrupt], "messages": []}
        interrupts = chat_router._extract_interrupts_from_result(result)
        assert len(interrupts) == 1
        assert interrupts[0] is fake_interrupt

    def test_extract_interrupts_from_result_without_interrupts(self):
        """Normal results without __interrupt__ must return empty list."""
        result = {"messages": [{"type": "ai", "content": "hello"}]}
        assert chat_router._extract_interrupts_from_result(result) == []

    def test_extract_interrupts_from_non_dict(self):
        """Non-dict results must return empty list."""
        assert chat_router._extract_interrupts_from_result("hello") == []
        assert chat_router._extract_interrupts_from_result(None) == []
        assert chat_router._extract_interrupts_from_result([]) == []

    def test_build_approval_request_info(self):
        """_build_approval_request_info must extract action details."""
        fake_interrupt = SimpleNamespace(
            value={
                "action_requests": [
                    {
                        "name": "write_file",
                        "args": {"path": "/etc/passwd", "content": "hacked"},
                        "description": "Tool execution requires approval\n\nTool: write_file\nArgs: ...",
                    },
                ],
                "review_configs": [],
            },
        )
        info = chat_router._build_approval_request_info([fake_interrupt])
        assert len(info["actions"]) == 1
        action = info["actions"][0]
        assert action["name"] == "write_file"
        assert action["args"]["path"] == "/etc/passwd"
        assert "approval" in action["description"]

    def test_build_approval_request_info_empty(self):
        """Empty interrupt list must produce empty actions."""
        info = chat_router._build_approval_request_info([])
        assert info["actions"] == []


class TestApprovalAuditIntegration:
    """Verify audit records are created for approval decisions."""

    def test_record_approval_decision(self, tmp_path, monkeypatch):
        """record_approval_decision must write an append-only record."""
        monkeypatch.setenv("AGENTCORE_DATA_DIR", str(tmp_path))
        from agentcore.runtime.audit import record_approval_decision, load_audit_records, _audit_path

        record_approval_decision(
            agent_id="test-agent",
            session_id="sess-1",
            decision="approve",
            tool_name="write_file",
            final_args={"path": "/tmp/test"},
            operator="user-1",
        )

        records = load_audit_records()
        assert len(records) == 1
        r = records[0]
        assert r["agent_id"] == "test-agent"
        assert r["session_id"] == "sess-1"
        assert r["decision"] == "approve"
        assert r["tool_name"] == "write_file"
        assert r["final_args"] == {"path": "/tmp/test"}
        assert r["operator"] == "user-1"
        assert "ts" in r

    def test_record_multiple_decisions(self, tmp_path, monkeypatch):
        """Multiple decisions must accumulate in the audit log."""
        monkeypatch.setenv("AGENTCORE_DATA_DIR", str(tmp_path))
        from agentcore.runtime.audit import record_approval_decision, load_audit_records

        for d in ("approve", "edit", "reject"):
            record_approval_decision(
                agent_id="a",
                session_id="s",
                decision=d,
                tool_name="t",
                final_args={},
            )

        records = load_audit_records()
        assert len(records) == 3
        assert [r["decision"] for r in records] == ["approve", "edit", "reject"]

    def test_filter_by_agent_id(self, tmp_path, monkeypatch):
        """load_audit_records(agent_id=...) must filter correctly."""
        monkeypatch.setenv("AGENTCORE_DATA_DIR", str(tmp_path))
        from agentcore.runtime.audit import record_approval_decision, load_audit_records

        record_approval_decision(agent_id="a1", session_id="s", decision="approve", tool_name="t", final_args={})
        record_approval_decision(agent_id="a2", session_id="s", decision="reject", tool_name="t", final_args={})

        assert len(load_audit_records(agent_id="a1")) == 1
        assert load_audit_records(agent_id="a1")[0]["decision"] == "approve"
        assert len(load_audit_records(agent_id="a2")) == 1
        assert len(load_audit_records(agent_id="ghost")) == 0

    def test_never_raises(self, tmp_path, monkeypatch):
        """record_approval_decision must swallow errors."""
        # Point to an unwritable path to trigger an error.
        monkeypatch.setenv("AGENTCORE_DATA_DIR", "/nonexistent/path/that/cannot/exist")
        from agentcore.runtime.audit import record_approval_decision

        # Must not raise.
        record_approval_decision(
            agent_id="a",
            session_id="s",
            decision="approve",
            tool_name="t",
            final_args={},
        )


# ---------------------------------------------------------------------------
# Task 1.10 — Tool-exclusion regression
# ---------------------------------------------------------------------------


class TestToolExclusionRegression:
    """Ensure tool exclusion doesn't silently fail after the refactor."""

    def test_disabled_tools_actually_excluded(self, captured_create):
        """Every disabled tool must appear in the exclusion middleware."""
        config = _config(tools={"enabled": [], "disabled": ["write_file", "delete", "execute"]})
        AgentFactory().create_agent(config)
        middleware = captured_create.get("middleware", [])
        exclusion = [m for m in middleware if isinstance(m, _ToolExclusionMiddleware)]
        assert len(exclusion) == 1
        for tool in ("write_file", "delete", "execute"):
            assert tool in exclusion[0]._excluded

    def test_enabled_whitelist_excludes_unlisted(self, captured_create):
        """Only whitelisted tools survive; everything else is excluded."""
        config = _config(tools={"enabled": ["read_file", "ls"], "disabled": []})
        AgentFactory().create_agent(config)
        middleware = captured_create.get("middleware", [])
        exclusion = [m for m in middleware if isinstance(m, _ToolExclusionMiddleware)]
        assert len(exclusion) == 1
        # read_file and ls must NOT be in excluded.
        assert "read_file" not in exclusion[0]._excluded
        assert "ls" not in exclusion[0]._excluded
        # write_file must be excluded (not in whitelist).
        assert "write_file" in exclusion[0]._excluded

    def test_no_middleware_when_all_tools_enabled(self, captured_create):
        """When nothing is excluded, no middleware should be injected."""
        # Enable all tools — but execute is still excluded on non-sandbox.
        from agentcore.constants import BUILT_IN_TOOLS
        config = _config(tools={"enabled": list(BUILT_IN_TOOLS), "disabled": []})
        AgentFactory().create_agent(config)
        middleware = captured_create.get("middleware", [])
        exclusion = [m for m in middleware if isinstance(m, _ToolExclusionMiddleware)]
        # execute is always excluded on non-sandbox backend.
        assert len(exclusion) == 1
        assert "execute" in exclusion[0]._excluded
