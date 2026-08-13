"""Subagent registry: aggregates loaded agents as deepagents ``SubAgent`` specs.

When an agent opts in via ``settings["enable_subagents"] = true`` in its
``agent.json``, the other loaded agents are injected into its graph as
deepagents SDK subagents (invokable through the SDK's ``task`` tool).

The registry is a thin, stateless view over the
:class:`~agentcore.runtime.multi_agent_manager.MultiAgentManager`'s loaded
workspaces — each call to :meth:`SubAgentRegistry.get_subagents` re-reads
the current set so newly loaded agents are picked up on the next graph
(re)build.

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
    ) -> list[SubAgent]:
        """Return :class:`SubAgent` specs for every loaded agent.

        Parameters
        ----------
        exclude_agent_id:
            Skip this agent — used when building an agent's own graph so
            it never delegates to itself.
        """
        subagents: list[SubAgent] = []
        workspaces: dict[str, Any] = getattr(self._manager, "_workspaces", {})
        for agent_id, workspace in workspaces.items():
            if agent_id == exclude_agent_id:
                continue
            try:
                system_prompt = workspace.get_system_prompt()
            except Exception:
                logger.exception(
                    "Failed to read system prompt for agent %s — skipped "
                    "as subagent",
                    agent_id,
                )
                continue
            subagents.append(
                SubAgent(
                    name=agent_id,
                    description=f"Agent: {agent_id}",
                    system_prompt=system_prompt or _EMPTY_PROMPT_FALLBACK,
                )
            )
        return subagents
