"""Regression tests for the unified agent-creation path.

Verifies that:

- ``AgentFactory`` consumes an ``agent.json`` configuration dict and
  applies tool exclusion via the SDK's ``_ToolExclusionMiddleware``.
- HITL ``interrupt_rules`` and filesystem ``permissions`` from the
  configuration's ``settings`` reach ``create_deep_agent``.
- ``chat_router`` resolves agent graphs through a supplied
  ``AgentFactory`` from the workspace's ``agent.json``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentcore.runtime import agent_factory as agent_factory_mod
from agentcore.runtime import chat_router
from agentcore.runtime.agent_factory import AgentFactory
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
    """Patch ``create_deep_agent`` inside the factory and capture kwargs."""
    captured: dict[str, Any] = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(agent_factory_mod, "create_deep_agent", fake_create)
    return captured


@pytest.fixture(autouse=True)
def _dummy_api_key(monkeypatch):
    """Provide a fake API key so ChatOpenAI construction never fails."""
    monkeypatch.setenv("AGENTCORE_LLM_API_KEY", "sk-test-dummy")


@pytest.fixture(autouse=True)
def _clear_graph_cache():
    chat_router._default_chat_state.graph_cache.clear()
    yield
    chat_router._default_chat_state.graph_cache.clear()


def _exclusion_middleware(middleware):
    local_cls = agent_factory_mod._ToolExclusionMiddleware
    return [m for m in middleware if isinstance(m, local_cls)]


# ---------------------------------------------------------------------------
# AgentFactory — tool exclusion via SDK middleware
# ---------------------------------------------------------------------------


def test_tool_exclusion_uses_public_middleware(captured_create):
    config = _config(tools={"enabled": [], "disabled": ["write_file"]})
    AgentFactory().create_agent(config)

    exclusion = _exclusion_middleware(captured_create.get("middleware", []))
    assert len(exclusion) == 1
    assert "write_file" in exclusion[0]._excluded
    # The middleware is now a public class in agent_factory (Task 1.7).
    assert hasattr(agent_factory_mod, "_ToolExclusionMiddleware")


def test_local_backend_always_excludes_execute(captured_create):
    # ``execute`` is only supported by the sandbox backend, so the default
    # local backend must exclude it even with an empty disabled list.
    # ``task`` is excluded as well because subagents are opt-in and no
    # subagent specs are injected when ``enable_subagents`` is false.
    AgentFactory().create_agent(_config())
    exclusion = _exclusion_middleware(captured_create.get("middleware", []))
    assert len(exclusion) == 1
    assert exclusion[0]._excluded == frozenset({"execute", "task"})


def test_no_extra_exclusion_on_sandbox_backend(captured_create):
    # Sandbox backend now raises SandboxUnavailableError when not configured.
    from agentcore.runtime.sandbox import SandboxUnavailableError
    config = _config(settings={"backend": {"type": "sandbox"}})
    with pytest.raises(SandboxUnavailableError):
        AgentFactory().create_agent(config)


def test_execute_excluded_even_when_explicitly_enabled(captured_create):
    # Sandbox hardening: execute must never reach a non-sandbox backend.
    config = _config(tools={"enabled": ["execute", "read_file"], "disabled": []})
    AgentFactory().create_agent(config)
    exclusion = _exclusion_middleware(captured_create.get("middleware", []))
    assert len(exclusion) == 1
    assert "execute" in exclusion[0]._excluded


def test_custom_root_dir_bypass_removed(captured_create, tmp_path):
    # settings.backend.root_dir must NOT create a FilesystemBackend
    # outside the workspace; without a workspace_dir the backend degrades
    # to the ephemeral StateBackend.
    config = _config(settings={"backend": {"root_dir": str(tmp_path / "evil")}})
    AgentFactory().create_agent(config)
    from deepagents.backends import StateBackend

    assert isinstance(captured_create.get("backend"), StateBackend)
    assert not (tmp_path / "evil").exists()


def test_workspace_backend_forced_virtual_mode(captured_create, tmp_path):
    workspace_dir = tmp_path / "ws"
    AgentFactory().create_agent(_config(), workspace_dir=workspace_dir)
    from deepagents.backends import FilesystemBackend

    backend = captured_create.get("backend")
    # Workspace backend is a plain FilesystemBackend (no CompositeBackend
    # wrapper).  The SDK's MemoryMiddleware loads memory files via the
    # backend's download_files method.
    assert isinstance(backend, FilesystemBackend)
    assert backend.virtual_mode is True
    assert Path(backend.cwd).resolve() == workspace_dir.resolve()


# ---------------------------------------------------------------------------
# AgentFactory — HITL and permissions pass-through
# ---------------------------------------------------------------------------


def test_interrupt_rules_mapped_to_interrupt_on(captured_create):
    config = _config(settings={
        "interrupt_rules": [
            {"tool_name": "write_file", "require_approval": True},
            {"tool_name": "read_file", "require_approval": False},
        ]
    })
    AgentFactory().create_agent(config)
    assert captured_create.get("interrupt_on") == {"write_file": True}


def test_permissions_mapped_to_filesystem_permissions(captured_create):
    config = _config(settings={
        "permissions": [
            {"operations": ["read", "write"], "paths": ["/**"], "mode": "allow"}
        ]
    })
    AgentFactory().create_agent(config)
    permissions = captured_create.get("permissions")
    assert permissions and len(permissions) == 1


# ---------------------------------------------------------------------------
# chat_router — delegation to AgentFactory via agent.json
# ---------------------------------------------------------------------------


class _FakeFactory:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def create_agent(self, agent_config, **kwargs):
        self.calls.append({"agent_config": agent_config, **kwargs})
        return object()


def _fake_workspace(config: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_dir=Path("ws/dir"),
        read_agent_config=lambda: config,
    )


async def test_chat_router_delegates_to_factory(monkeypatch):
    async def _fake_checkpointer(chat_state):
        return object()

    monkeypatch.setattr(chat_router, "get_checkpointer", _fake_checkpointer)
    config = _config()
    workspace = _fake_workspace(config)
    factory = _FakeFactory()

    graph = await chat_router._resolve_agent_graph("agent-x", workspace, factory=factory)
    assert graph is not None
    assert len(factory.calls) == 1
    call = factory.calls[0]
    assert call["agent_config"] is config
    assert call["checkpointer"] is not None
    # Cached — second lookup must not rebuild.
    assert await chat_router._resolve_agent_graph("agent-x", workspace, factory=factory) is graph
    assert len(factory.calls) == 1


async def test_chat_router_passes_workspace_dir(monkeypatch):
    async def _fake_checkpointer(chat_state):
        return object()

    monkeypatch.setattr(chat_router, "get_checkpointer", _fake_checkpointer)
    workspace = _fake_workspace(_config())
    factory = _FakeFactory()

    await chat_router._resolve_agent_graph("agent-y", workspace, factory=factory)
    assert factory.calls[0]["workspace_dir"] == Path("ws/dir")
    assert factory.calls[0]["workspace"] is workspace
