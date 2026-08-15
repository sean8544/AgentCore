"""Subagent registry: aggregates loaded agents as deepagents ``SubAgent`` specs.

When an agent opts in via ``settings["enable_subagents"] = true`` in its
``agent.json``, the other loaded agents are injected into its graph as
deepagents SDK subagents (invokable through the SDK's ``task`` tool).

The registry reads each workspace's ``agent.json`` configuration and
maps the full SubAgent field set — tools, model, permissions,
interrupt_on, skills — so subagents carry their own specialist
configuration rather than inheriting the parent's defaults.

Note: subagents are **opt-in** (default disabled) so agents are not
mutually visible unless explicitly configured.
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents.middleware.subagents import SubAgent

logger = logging.getLogger(__name__)

# Fallback prompt when a workspace has no kernel files at all — the SDK
# requires a non-empty ``system_prompt`` on every SubAgent spec.
_EMPTY_PROMPT_FALLBACK = "You are a helpful AI assistant."


class SubAgentRegistry:
    """Aggregate all loaded agents as :class:`SubAgent` specs.

    Parameters
    ----------
    manager:
        A :class:`~agentcore.runtime.multi_agent_manager.MultiAgentManager`
        (or any object exposing a ``_workspaces`` mapping of
        ``agent_id -> Workspace``).
    """

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def get_subagents(
        self,
        exclude_agent_id: str | None = None,
        whitelist: list[str] | None = None,
    ) -> list[SubAgent]:
        """Return :class:`SubAgent` specs for loaded agents.

        Each spec carries the agent's full specialist configuration
        (model, permissions, interrupt_on, skills) so the subagent
        executes with its own settings rather than the parent's.

        Parameters
        ----------
        exclude_agent_id:
            Skip this agent — used when building an agent's own graph so
            it never delegates to itself.
        whitelist:
            If provided, only include agents whose IDs are in this list.
            When ``None`` or empty, all loaded agents are included
            (backward-compatible behavior).
        """
        subagents: list[SubAgent] = []
        # Use the public API if available, fall back to private attribute
        # for backward compatibility.  Check the result type to avoid
        # MagicMock false-positives in tests.
        get_loaded = getattr(self._manager, "get_loaded_workspaces", None)
        if callable(get_loaded):
            result = get_loaded()
            if isinstance(result, dict):
                workspaces = result
            else:
                workspaces = getattr(self._manager, "_workspaces", {})
        else:
            workspaces = getattr(self._manager, "_workspaces", {})
        for agent_id, workspace in workspaces.items():
            if agent_id == exclude_agent_id:
                continue
            # Apply whitelist filter if specified
            if whitelist and agent_id not in whitelist:
                continue
            try:
                spec = self._build_subagent_spec(agent_id, workspace)
                subagents.append(spec)
            except Exception:
                logger.exception(
                    "Failed to build subagent spec for %s — skipped",
                    agent_id,
                )
        return subagents

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_subagent_spec(
        self, agent_id: str, workspace: Any
    ) -> SubAgent:
        """Build a full :class:`SubAgent` spec from a workspace."""
        try:
            system_prompt = workspace.get_system_prompt()
        except Exception:
            logger.warning(
                "Failed to read system prompt for agent %s — using fallback",
                agent_id,
            )
            system_prompt = None

        # Read the agent.json config for full field mapping.
        agent_config = workspace.read_agent_config()
        settings = agent_config.get("settings", {}) or {}

        # Merge global approval rules (system security settings) so the
        # subagent's interrupt_on also obeys the global configuration.
        from agentcore.runtime.security_router import apply_global_approval

        settings = apply_global_approval(settings)

        spec: SubAgent = {
            "name": agent_id,
            "description": self._generate_description(agent_id, agent_config),
            "system_prompt": system_prompt or _EMPTY_PROMPT_FALLBACK,
        }

        # --- Model (Task 2.1) ----------------------------------------
        # Resolve to a fully-constructed BaseChatModel instance (via
        # model_factory) so the subagent carries its own base_url and
        # api_key — passing a bare "provider:model" string would force
        # the SDK to re-resolve credentials, which fails for custom
        # endpoints (e.g. DashScope) that rely on api_key_env.
        model_obj = self._resolve_model_object(agent_config)
        if model_obj is not None:
            spec["model"] = model_obj

        # --- Permissions (Task 2.1) -----------------------------------
        perm_cfgs = settings.get("permissions", [])
        if perm_cfgs:
            from agentcore.runtime.agent_factory import _map_permissions

            permissions = _map_permissions(perm_cfgs)
            if permissions:
                spec["permissions"] = permissions

        # --- Interrupt-on / HITL (Task 2.1) --------------------------
        interrupt_rules = settings.get("interrupt_rules", [])
        if interrupt_rules:
            from agentcore.runtime.agent_factory import _map_interrupt_on

            interrupt_on = _map_interrupt_on(interrupt_rules)
            if interrupt_on:
                spec["interrupt_on"] = interrupt_on

        # --- Skills (Task 2.1) ---------------------------------------
        from agentcore.runtime.workspace import SKILLS_DIR_NAME

        skills_dir = workspace.workspace_dir / SKILLS_DIR_NAME
        if skills_dir.exists() and skills_dir.is_dir():
            spec["skills"] = [f"/{SKILLS_DIR_NAME}/"]

        # --- Tools (inheritance control) --------------------------------
        # SDK §4.8 semantics: specifying ``tools`` on a SubAgent fully
        # overrides the parent agent's tool set.  When
        # ``inherit_parent_tools`` is explicitly set to false the
        # subagent's own tool config is mapped; otherwise (the default)
        # tools are inherited from the parent (no ``tools`` key in spec).
        inherit_tools = settings.get("inherit_parent_tools", True)
        if not inherit_tools:
            tools_cfg = agent_config.get("tools", {}) or {}
            enabled = tools_cfg.get("enabled", []) or []
            disabled = tools_cfg.get("disabled", []) or []
            if enabled or disabled:
                # Build the effective tool list: enabled acts as a
                # whitelist; disabled items are excluded.
                if enabled:
                    spec["tools"] = [
                        t for t in enabled if t not in disabled
                    ]
                else:
                    # Only disabled list provided — cannot express as a
                    # positive whitelist without knowing the full tool
                    # surface; skip mapping (parent tools inherited).
                    pass

        return spec

    @staticmethod
    def _resolve_model_object(agent_config: dict[str, Any]) -> Any | None:
        """Convert agent.json model config to a BaseChatModel instance.

        Uses :func:`model_factory.resolve_model` which returns a
        ``ChatOpenAI`` (with api_key + base_url) when possible, falling
        back to a ``provider:model`` string.
        """
        from agentcore.runtime.model_factory import resolve_model

        model_cfg = agent_config.get("model") or {}
        if not model_cfg.get("name"):
            return None
        return resolve_model(model_cfg)

    @staticmethod
    def _resolve_model_string(agent_config: dict[str, Any]) -> str | None:
        """Convert agent.json model config to 'provider:model' format."""
        model_cfg = agent_config.get("model") or {}
        provider = model_cfg.get("provider", "")
        name = model_cfg.get("name", "")
        if not name:
            return None
        if provider:
            return f"{provider}:{name}"
        return name

    @staticmethod
    def _generate_description(
        agent_id: str, agent_config: dict[str, Any]
    ) -> str:
        """Generate a capability-aware delegation description (Task 2.2).

        Uses the workspace description field (if present) plus a tool
        capability summary so the main agent's LLM can decide when to
        delegate.
        """
        settings = agent_config.get("settings", {}) or {}
        # Merge global approval rules so the HITL hint reflects the
        # system-wide security configuration too.
        from agentcore.runtime.security_router import apply_global_approval

        settings = apply_global_approval(settings)
        description = settings.get("description", "")

        # Build a tool capability summary.
        tools_cfg = agent_config.get("tools", {}) or {}
        enabled = tools_cfg.get("enabled", []) or []
        disabled = tools_cfg.get("disabled", []) or []

        parts: list[str] = []
        if description:
            parts.append(description)
        else:
            parts.append(f"Agent '{agent_id}'")

        if enabled:
            parts.append(f"Specialist tools: {', '.join(enabled)}.")
        elif disabled:
            parts.append(f"Excluded tools: {', '.join(disabled)}.")

        # Interrupt rules hint.
        interrupt_rules = settings.get("interrupt_rules", [])
        if interrupt_rules:
            hitl_tools = [
                r.get("tool_name", "")
                for r in interrupt_rules
                if r.get("require_approval")
            ]
            if hitl_tools:
                parts.append(f"HITL required for: {', '.join(hitl_tools)}.")

        return " ".join(parts)
