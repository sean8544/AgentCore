"""Tests for SubAgent specialization (Task 2.7) and dynamic discovery (Task 2.8).

Covers:
- Task 2.7: SubAgent full-field mapping — model, permissions, interrupt_on,
  skills, and dynamic description generation.
- Task 2.8: Dynamic discovery — new agents are picked up by subagent
  consumers after creation / reload.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.runtime.subagent_registry import SubAgentRegistry


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_workspace(
    *,
    agent_id: str = "helper",
    workspace_dir: Path | None = None,
    agent_config: dict[str, Any] | None = None,
    system_prompt: str = "You are a helpful assistant.",
    has_skills_dir: bool = False,
) -> MagicMock:
    """Create a mock workspace with configurable agent.json."""
    ws = MagicMock()
    ws.workspace_dir = workspace_dir or Path("/tmp/fake")
    ws.get_system_prompt.return_value = system_prompt

    cfg = agent_config or {
        "model": {"provider": "openai", "name": "gpt-4o-mini"},
        "tools": {"enabled": [], "disabled": []},
        "settings": {},
    }
    ws.read_agent_config.return_value = cfg

    # Skills directory detection: patch Path.exists/is_dir on the skills path.
    if has_skills_dir:
        original_path_exists = Path.exists

        def _skills_aware_exists(p: Path) -> bool:
            if str(p).endswith("/skills") or str(p).endswith("\\skills"):
                return True
            return original_path_exists(p)

        ws.workspace_dir.__truediv__ = lambda self, key: (
            MagicMock(
                exists=lambda: True,
                is_dir=lambda: True,
            )
            if key == "skills"
            else Path(self, key) if isinstance(self, str) else Path(str(self)) / key
        )

    return ws


def _make_manager(workspaces: dict[str, Any]) -> MagicMock:
    """Create a mock manager with a _workspaces mapping."""
    manager = MagicMock()
    manager._workspaces = workspaces
    return manager


# ---------------------------------------------------------------------------
# Task 2.7 — SubAgent specialization: model mapping
# ---------------------------------------------------------------------------


class TestSubAgentModelMapping:
    """Verify model is mapped as 'provider:model' string."""

    def test_model_provider_name_format(self):
        """Model with provider+name must produce 'provider:name'."""
        ws = _make_workspace(
            agent_config={
                "model": {"provider": "openai", "name": "gpt-4o"},
                "tools": {},
                "settings": {},
            }
        )
        manager = _make_manager({"helper": ws})
        registry = SubAgentRegistry(manager)
        specs = registry.get_subagents()

        assert len(specs) == 1
        assert specs[0]["model"] == "openai:gpt-4o"

    def test_model_name_only(self):
        """Model with name but no provider must use just the name."""
        ws = _make_workspace(
            agent_config={
                "model": {"name": "local-llm"},
                "tools": {},
                "settings": {},
            }
        )
        manager = _make_manager({"helper": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        assert specs[0]["model"] == "local-llm"

    def test_model_missing_name_omitted(self):
        """When model has no name, the model field must be omitted."""
        ws = _make_workspace(
            agent_config={
                "model": {"provider": "openai"},
                "tools": {},
                "settings": {},
            }
        )
        manager = _make_manager({"helper": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        assert "model" not in specs[0]

    def test_empty_model_config_omitted(self):
        """Empty model config must not add model key."""
        ws = _make_workspace(
            agent_config={"model": {}, "tools": {}, "settings": {}}
        )
        manager = _make_manager({"helper": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        assert "model" not in specs[0]


# ---------------------------------------------------------------------------
# Task 2.7 — SubAgent specialization: permissions mapping
# ---------------------------------------------------------------------------


class TestSubAgentPermissions:
    """Verify permissions from agent.json are mapped to SubAgent spec."""

    def test_permissions_mapped(self):
        """Permission rules must appear in the SubAgent spec."""
        ws = _make_workspace(
            agent_config={
                "model": {"provider": "openai", "name": "gpt-4o"},
                "tools": {},
                "settings": {
                    "permissions": [
                        {
                            "operations": ["read"],
                            "paths": ["/data/*"],
                            "mode": "allow",
                        },
                        {
                            "operations": ["write"],
                            "paths": ["/tmp/*"],
                            "mode": "deny",
                        },
                    ],
                },
            }
        )
        manager = _make_manager({"helper": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        assert "permissions" in specs[0]
        perms = specs[0]["permissions"]
        assert len(perms) == 2
        assert perms[0].mode == "allow"
        assert perms[1].mode == "deny"

    def test_empty_permissions_omitted(self):
        """No permissions config must omit the field."""
        ws = _make_workspace(
            agent_config={
                "model": {"provider": "openai", "name": "gpt-4o"},
                "tools": {},
                "settings": {},
            }
        )
        manager = _make_manager({"helper": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        assert "permissions" not in specs[0]


# ---------------------------------------------------------------------------
# Task 2.7 — SubAgent specialization: interrupt_on mapping
# ---------------------------------------------------------------------------


class TestSubAgentInterruptOn:
    """Verify interrupt_rules are mapped to interrupt_on in SubAgent spec."""

    def test_interrupt_on_mapped(self):
        """interrupt_rules with require_approval must produce interrupt_on."""
        ws = _make_workspace(
            agent_config={
                "model": {"provider": "openai", "name": "gpt-4o"},
                "tools": {},
                "settings": {
                    "interrupt_rules": [
                        {"tool_name": "write_file", "require_approval": True},
                        {"tool_name": "execute", "require_approval": True},
                    ],
                },
            }
        )
        manager = _make_manager({"helper": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        assert "interrupt_on" in specs[0]
        assert specs[0]["interrupt_on"] == {
            "write_file": True,
            "execute": True,
        }

    def test_no_interrupt_rules_omitted(self):
        """No interrupt_rules must omit interrupt_on."""
        ws = _make_workspace(
            agent_config={
                "model": {"provider": "openai", "name": "gpt-4o"},
                "tools": {},
                "settings": {},
            }
        )
        manager = _make_manager({"helper": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        assert "interrupt_on" not in specs[0]


# ---------------------------------------------------------------------------
# Task 2.7 — SubAgent specialization: skills detection
# ---------------------------------------------------------------------------


class TestSubAgentSkills:
    """Verify skills/ directory detection adds skills to SubAgent spec."""

    def test_skills_detected_when_dir_exists(self, tmp_path):
        """When workspace has a skills/ dir, spec must include skills."""
        ws_dir = tmp_path / "agent_ws"
        ws_dir.mkdir()
        skills_dir = ws_dir / "skills"
        skills_dir.mkdir()

        ws = _make_workspace(workspace_dir=ws_dir)
        manager = _make_manager({"helper": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        assert "skills" in specs[0]
        assert specs[0]["skills"] == ["/skills/"]

    def test_no_skills_dir_omitted(self, tmp_path):
        """Without a skills/ dir, the field must be omitted."""
        ws_dir = tmp_path / "agent_ws"
        ws_dir.mkdir()
        # No skills/ directory created.

        ws = _make_workspace(workspace_dir=ws_dir)
        manager = _make_manager({"helper": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        assert "skills" not in specs[0]


# ---------------------------------------------------------------------------
# Task 2.7 — Dynamic description generation
# ---------------------------------------------------------------------------


class TestSubAgentDynamicDescription:
    """Verify description is generated from workspace config, not template."""

    def test_description_uses_settings_description(self):
        """When settings.description is present, it must appear in output."""
        ws = _make_workspace(
            agent_config={
                "model": {},
                "tools": {},
                "settings": {"description": "Expert code reviewer"},
            }
        )
        manager = _make_manager({"helper": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        assert "Expert code reviewer" in specs[0]["description"]
        assert "Agent:" not in specs[0]["description"]  # No template fallback

    def test_description_fallback_to_agent_id(self):
        """Without settings.description, use agent_id-based fallback."""
        ws = _make_workspace(
            agent_config={"model": {}, "tools": {}, "settings": {}}
        )
        manager = _make_manager({"my-agent": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        assert "my-agent" in specs[0]["description"]

    def test_description_includes_tool_summary(self):
        """Enabled tools must appear in the description."""
        ws = _make_workspace(
            agent_config={
                "model": {},
                "tools": {"enabled": ["read_file", "write_file"], "disabled": []},
                "settings": {"description": "File specialist"},
            }
        )
        manager = _make_manager({"helper": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        desc = specs[0]["description"]
        assert "read_file" in desc
        assert "write_file" in desc

    def test_description_includes_hitl_hint(self):
        """interrupt_rules must add HITL hint to description."""
        ws = _make_workspace(
            agent_config={
                "model": {},
                "tools": {},
                "settings": {
                    "description": "Careful agent",
                    "interrupt_rules": [
                        {"tool_name": "delete", "require_approval": True}
                    ],
                },
            }
        )
        manager = _make_manager({"helper": ws})
        specs = SubAgentRegistry(manager).get_subagents()

        desc = specs[0]["description"]
        assert "HITL required for: delete" in desc


# ---------------------------------------------------------------------------
# Task 2.7 — Exclude self (anti-self-delegation)
# ---------------------------------------------------------------------------


class TestSubAgentExcludeSelf:
    """Verify the main agent is excluded from its own subagent list."""

    def test_exclude_agent_id(self):
        """exclude_agent_id must skip that agent."""
        ws_a = _make_workspace(agent_id="a")
        ws_b = _make_workspace(agent_id="b")
        ws_c = _make_workspace(agent_id="c")
        manager = _make_manager({"a": ws_a, "b": ws_b, "c": ws_c})

        specs = SubAgentRegistry(manager).get_subagents(exclude_agent_id="b")
        names = [s["name"] for s in specs]

        assert "b" not in names
        assert "a" in names
        assert "c" in names


# ---------------------------------------------------------------------------
# Task 2.8 — Dynamic discovery
# ---------------------------------------------------------------------------


class TestDynamicDiscovery:
    """Verify _invalidate_subagent_consumers invalidates the right agents."""

    def test_invalidate_only_subagent_consumers(self):
        """Only agents with enable_subagents=true should be invalidated."""
        import asyncio
        from agentcore.runtime import agent_router

        invalidated: list[str] = []

        async def fake_invalidate(state: Any, agent_id: str | None = None) -> None:
            invalidated.append(agent_id or "all")

        # Build a mock request with workspaces.
        ws_main = MagicMock()
        ws_main.read_agent_config.return_value = {
            "settings": {"enable_subagents": True}
        }
        ws_other = MagicMock()
        ws_other.read_agent_config.return_value = {
            "settings": {"enable_subagents": False}
        }
        ws_third = MagicMock()
        ws_third.read_agent_config.return_value = {
            "settings": {"enable_subagents": True}
        }

        manager = MagicMock()
        manager._workspaces = {
            "main": ws_main,
            "other": ws_other,
            "third": ws_third,
        }

        request = MagicMock()
        request.app.state.agent_manager = manager

        with patch.object(
            agent_router, "invalidate_agent_graph", side_effect=fake_invalidate
        ):
            asyncio.run(agent_router._invalidate_subagent_consumers(request))

        # "main" and "third" have enable_subagents=true; "other" does not.
        assert "main" in invalidated
        assert "third" in invalidated
        assert "other" not in invalidated

    def test_invalidate_handles_missing_manager(self):
        """When no manager is on state, the function must be a no-op."""
        import asyncio
        from agentcore.runtime import agent_router

        request = MagicMock()
        request.app.state = SimpleNamespace()  # No agent_manager

        # Must not raise.
        asyncio.run(agent_router._invalidate_subagent_consumers(request))

    def test_invalidate_tolerates_config_read_errors(self):
        """If read_agent_config fails for one workspace, others proceed."""
        import asyncio
        from agentcore.runtime import agent_router

        invalidated: list[str] = []

        async def fake_invalidate(state: Any, agent_id: str | None = None) -> None:
            invalidated.append(agent_id or "all")

        ws_good = MagicMock()
        ws_good.read_agent_config.return_value = {
            "settings": {"enable_subagents": True}
        }
        ws_bad = MagicMock()
        ws_bad.read_agent_config.side_effect = Exception("corrupt config")

        manager = MagicMock()
        manager._workspaces = {"good": ws_good, "bad": ws_bad}

        request = MagicMock()
        request.app.state.agent_manager = manager

        with patch.object(
            agent_router, "invalidate_agent_graph", side_effect=fake_invalidate
        ):
            asyncio.run(agent_router._invalidate_subagent_consumers(request))

        assert "good" in invalidated
        assert "bad" not in invalidated

    def test_new_agent_appears_in_subagent_list(self, tmp_path):
        """After adding a new workspace, the registry must include it."""
        ws_a = _make_workspace(workspace_dir=tmp_path / "a")
        ws_b = _make_workspace(workspace_dir=tmp_path / "b")
        manager = _make_manager({"a": ws_a, "b": ws_b})

        specs_before = SubAgentRegistry(manager).get_subagents(
            exclude_agent_id="a"
        )
        names_before = {s["name"] for s in specs_before}
        assert "b" in names_before
        assert "c" not in names_before

        # Simulate creating a new agent "c".
        ws_c = _make_workspace(workspace_dir=tmp_path / "c")
        manager._workspaces["c"] = ws_c

        specs_after = SubAgentRegistry(manager).get_subagents(
            exclude_agent_id="a"
        )
        names_after = {s["name"] for s in specs_after}
        assert "c" in names_after
        assert "b" in names_after

    def test_registry_rebuilds_after_invalidation(self, tmp_path):
        """After graph invalidation, the next get_subagents() picks up changes."""
        ws_a = _make_workspace(
            agent_id="a",
            workspace_dir=tmp_path / "a",
            agent_config={
                "model": {"provider": "openai", "name": "gpt-4o"},
                "tools": {},
                "settings": {"description": "Original description"},
            },
        )
        manager = _make_manager({"a": ws_a})

        specs = SubAgentRegistry(manager).get_subagents()
        assert "Original description" in specs[0]["description"]

        # Simulate config update (e.g., after reload).
        ws_a.read_agent_config.return_value = {
            "model": {"provider": "anthropic", "name": "claude-3"},
            "tools": {},
            "settings": {"description": "Updated description"},
        }

        # After invalidation + rebuild, the new config is reflected.
        specs_new = SubAgentRegistry(manager).get_subagents()
        assert "Updated description" in specs_new[0]["description"]
        assert specs_new[0]["model"] == "anthropic:claude-3"
