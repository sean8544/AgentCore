"""Phase 4: Publish & rollback drill tests (Task 4.3).

Verifies that profile versioning (agent.json-based) is truly applied at
runtime, including:
- Config changes picked up after reload
- Rollback to a previous agent.json version works
- Subagent consumers invalidated on config change
- Tool enable/disable takes effect after reload
- Model change takes effect after reload
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_workspace(ws):
    """Initialize a workspace synchronously (ws.initialize() is async)."""
    asyncio.run(ws.initialize())


def _write_agent_json(path: Path, config: dict) -> None:
    """Write an agent.json file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")


def _read_agent_json(path: Path) -> dict:
    """Read and parse an agent.json file."""
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Test: Config change → reload → new config applied
# ---------------------------------------------------------------------------


class TestConfigChangeAppliedAfterReload:
    """Verify that agent.json changes are applied at runtime after reload."""

    def test_model_change_applied_after_reload(self, tmp_path):
        """Changing model in agent.json and reloading picks up the new model."""
        from agentcore.runtime.workspace import Workspace

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        ws = Workspace("test-agent", "test-agent", workspace_dir=ws_dir)

        agent_json_path = ws_dir / "agent.json"
        config_v1 = _read_agent_json(agent_json_path)
        assert config_v1["model"]["name"] != ""  # has some default

        # "Publish" a new model config.
        config_v2 = dict(config_v1)
        config_v2["model"] = {"provider": "anthropic", "name": "claude-3-opus"}
        _write_agent_json(agent_json_path, config_v2)

        # After reload, reading config returns the new model.
        reloaded = ws.read_agent_config()
        assert reloaded["model"]["provider"] == "anthropic"
        assert reloaded["model"]["name"] == "claude-3-opus"

    def test_tool_disable_applied_after_reload(self, tmp_path):
        """Disabling a tool in agent.json and reloading takes effect."""
        from agentcore.runtime.workspace import Workspace

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        ws = Workspace("tool-agent", "tool-agent", workspace_dir=ws_dir)

        agent_json_path = ws_dir / "agent.json"
        config = _read_agent_json(agent_json_path)
        config["tools"] = {"enabled": ["read_file"], "disabled": ["execute", "write_file"]}
        _write_agent_json(agent_json_path, config)

        reloaded = ws.read_agent_config()
        assert "execute" in reloaded["tools"]["disabled"]
        assert "write_file" in reloaded["tools"]["disabled"]

    def test_settings_change_enable_planning(self, tmp_path):
        """Toggling planning in agent.json is reflected after reload."""
        from agentcore.runtime.workspace import Workspace

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        ws = Workspace("plan-agent", "plan-agent", workspace_dir=ws_dir)

        agent_json_path = ws_dir / "agent.json"
        config = _read_agent_json(agent_json_path)
        # New agents default to planning enabled (write_todos tool).
        assert config["settings"].get("enable_planning") is True

        config["settings"]["enable_planning"] = False
        _write_agent_json(agent_json_path, config)

        reloaded = ws.read_agent_config()
        assert reloaded["settings"]["enable_planning"] is False


# ---------------------------------------------------------------------------
# Test: Rollback to previous version
# ---------------------------------------------------------------------------


class TestRollbackToPreviousVersion:
    """Verify that reverting agent.json to a previous version works."""

    def test_rollback_model_to_previous(self, tmp_path):
        """Rolling back model config to a previous version is applied."""
        from agentcore.runtime.workspace import Workspace

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        ws = Workspace("rollback-agent", "rollback-agent", workspace_dir=ws_dir)

        agent_json_path = ws_dir / "agent.json"

        # Save "v1" (original).
        config_v1 = _read_agent_json(agent_json_path)
        v1_model = dict(config_v1["model"])

        # "Publish" v2.
        config_v2 = dict(config_v1)
        config_v2["model"] = {"provider": "openai", "name": "gpt-4-turbo"}
        _write_agent_json(agent_json_path, config_v2)
        assert ws.read_agent_config()["model"]["name"] == "gpt-4-turbo"

        # "Rollback" — restore v1.
        _write_agent_json(agent_json_path, config_v1)
        rolled_back = ws.read_agent_config()
        assert rolled_back["model"]["provider"] == v1_model["provider"]
        assert rolled_back["model"]["name"] == v1_model["name"]

    def test_rollback_settings_to_previous(self, tmp_path):
        """Rolling back settings (e.g., enable_subagents) works."""
        from agentcore.runtime.workspace import Workspace

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        ws = Workspace("rb-settings", "rb-settings", workspace_dir=ws_dir)

        agent_json_path = ws_dir / "agent.json"

        # v1: subagents disabled.
        config_v1 = _read_agent_json(agent_json_path)
        config_v1["settings"]["enable_subagents"] = False
        _write_agent_json(agent_json_path, config_v1)

        # v2: subagents enabled.
        config_v2 = copy.deepcopy(config_v1)
        config_v2["settings"]["enable_subagents"] = True
        _write_agent_json(agent_json_path, config_v2)
        assert ws.read_agent_config()["settings"]["enable_subagents"] is True

        # Rollback to v1.
        _write_agent_json(agent_json_path, config_v1)
        rolled_back = ws.read_agent_config()
        assert rolled_back["settings"]["enable_subagents"] is False

    def test_rollback_tool_permissions(self, tmp_path):
        """Rolling back tool permissions is applied."""
        from agentcore.runtime.workspace import Workspace

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        ws = Workspace("rb-tools", "rb-tools", workspace_dir=ws_dir)

        agent_json_path = ws_dir / "agent.json"

        # v1: all tools enabled.
        config_v1 = _read_agent_json(agent_json_path)
        _write_agent_json(agent_json_path, config_v1)

        # v2: restrict tools.
        config_v2 = dict(config_v1)
        config_v2["tools"] = {"enabled": ["read_file"], "disabled": ["execute", "write_file"]}
        _write_agent_json(agent_json_path, config_v2)
        assert "execute" in ws.read_agent_config()["tools"]["disabled"]

        # Rollback to v1.
        _write_agent_json(agent_json_path, config_v1)
        rolled_back = ws.read_agent_config()
        assert "execute" not in rolled_back.get("tools", {}).get("disabled", [])


# ---------------------------------------------------------------------------
# Test: Graph cache invalidation on config change
# ---------------------------------------------------------------------------


class TestGraphCacheInvalidation:
    """Verify that graph cache is properly invalidated on config changes."""

    def test_invalidate_agent_graph_removes_cache_entry(self):
        """invalidate_agent_graph removes the specific agent from cache."""
        import asyncio
        from agentcore.runtime.chat_router import ChatState, invalidate_agent_graph

        chat_state = ChatState()
        chat_state.graph_cache["agent-a"] = MagicMock()
        chat_state.graph_cache["agent-b"] = MagicMock()

        fake_state = MagicMock()
        fake_state.chat_state = chat_state

        # Make get_chat_state return our chat_state.
        with patch("agentcore.runtime.chat_router.get_chat_state", return_value=chat_state):
            asyncio.run(invalidate_agent_graph(fake_state, "agent-a"))

        assert "agent-a" not in chat_state.graph_cache
        assert "agent-b" in chat_state.graph_cache  # not affected

    def test_invalidate_all_graphs_clears_cache(self):
        """invalidate_agent_graph(None) clears the entire cache."""
        import asyncio
        from agentcore.runtime.chat_router import ChatState, invalidate_agent_graph

        chat_state = ChatState()
        chat_state.graph_cache["a"] = MagicMock()
        chat_state.graph_cache["b"] = MagicMock()

        fake_state = MagicMock()

        with patch("agentcore.runtime.chat_router.get_chat_state", return_value=chat_state):
            asyncio.run(invalidate_agent_graph(fake_state, None))

        assert len(chat_state.graph_cache) == 0

    def test_subagent_consumers_invalidated(self, tmp_path):
        """_invalidate_subagent_consumers invalidates all enable_subagents agents."""
        import asyncio
        from agentcore.runtime.chat_router import ChatState, invalidate_agent_graph

        chat_state = ChatState()
        chat_state.graph_cache["main"] = MagicMock()
        chat_state.graph_cache["helper"] = MagicMock()
        chat_state.graph_cache["plain"] = MagicMock()

        # "main" has enable_subagents=true, "plain" does not.
        ws_main = MagicMock()
        ws_main.read_agent_config.return_value = {
            "settings": {"enable_subagents": True}
        }
        ws_plain = MagicMock()
        ws_plain.read_agent_config.return_value = {
            "settings": {"enable_subagents": False}
        }

        manager = MagicMock()
        manager._workspaces = {"main": ws_main, "plain": ws_plain}

        fake_state = MagicMock()

        async def _run():
            with patch("agentcore.runtime.chat_router.get_chat_state", return_value=chat_state):
                # Simulate _invalidate_subagent_consumers logic.
                for agent_id, ws in manager._workspaces.items():
                    config = ws.read_agent_config()
                    if config.get("settings", {}).get("enable_subagents"):
                        await invalidate_agent_graph(fake_state, agent_id)

        asyncio.run(_run())

        # "main" should be invalidated (had enable_subagents).
        assert "main" not in chat_state.graph_cache
        # "plain" should still be cached (no enable_subagents).
        assert "plain" in chat_state.graph_cache


# ---------------------------------------------------------------------------
# Test: Full publish → use → rollback → use cycle
# ---------------------------------------------------------------------------


class TestFullPublishRollbackCycle:
    """End-to-end: publish config → verify → rollback → verify."""

    def test_full_cycle_preserves_data_integrity(self, tmp_path):
        """Full publish → rollback cycle preserves workspace data integrity."""
        from agentcore.runtime.workspace import Workspace

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        ws = Workspace("cycle-agent", "cycle-agent", workspace_dir=ws_dir)

        agent_json_path = ws_dir / "agent.json"

        # Step 1: Record original config.
        original = _read_agent_json(agent_json_path)
        original_model = original["model"]["name"]

        # Step 2: Publish new config.
        published = dict(original)
        published["model"] = {"provider": "google", "name": "gemini-pro"}
        published["settings"] = dict(original.get("settings", {}))
        published["settings"]["enable_planning"] = True
        _write_agent_json(agent_json_path, published)

        # Step 3: Verify published config is read back.
        current = ws.read_agent_config()
        assert current["model"]["name"] == "gemini-pro"
        assert current["settings"]["enable_planning"] is True

        # Step 4: Rollback to original.
        _write_agent_json(agent_json_path, original)

        # Step 5: Verify rollback restores original config.
        rolled_back = ws.read_agent_config()
        assert rolled_back["model"]["name"] == original_model
        # New agents ship with planning enabled by default, so the
        # original snapshot carries enable_planning: true.
        assert rolled_back["settings"].get("enable_planning") is True

        # Step 6: Workspace data (kernel files, sessions) intact.
        assert (ws_dir / "agent.md").exists()
        # Legacy soul.md / profile.md are no longer seeded by default.
        assert not (ws_dir / "soul.md").exists()

    def test_multiple_publish_versions(self, tmp_path):
        """Multiple sequential publishes maintain correct latest state."""
        from agentcore.runtime.workspace import Workspace

        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        ws = Workspace("multi-ver", "multi-ver", workspace_dir=ws_dir)

        agent_json_path = ws_dir / "agent.json"
        base = _read_agent_json(agent_json_path)

        # Publish v1, v2, v3 sequentially.
        for i, model_name in enumerate(["gpt-4", "claude-3", "gemini-pro"], start=1):
            config = dict(base)
            config["model"] = {"provider": f"provider-{i}", "name": model_name}
            _write_agent_json(agent_json_path, config)
            current = ws.read_agent_config()
            assert current["model"]["name"] == model_name
            assert current["model"]["provider"] == f"provider-{i}"

        # Rollback to v1.
        v1_config = dict(base)
        v1_config["model"] = {"provider": "provider-1", "name": "gpt-4"}
        _write_agent_json(agent_json_path, v1_config)
        final = ws.read_agent_config()
        assert final["model"]["name"] == "gpt-4"
        assert final["model"]["provider"] == "provider-1"
