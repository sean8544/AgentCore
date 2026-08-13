"""Agent service: agent CRUD and lifecycle orchestration.

The former Profile governance layer (draft → compile → publish → rollback)
has been removed.  Agent configuration now lives in each workspace's
``agent.json`` file; :class:`AgentService` orchestrates workspace creation,
configuration writes, and runtime start/stop.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agentcore.repository import ControlPlaneStore
from agentcore.runtime.workspace import DEFAULT_AGENT_JSON


class AgentService:
    """Core orchestration service for agent lifecycle workflows."""

    def __init__(
        self,
        store: ControlPlaneStore,
        runtime: Any | None = None,
        agent_manager: Any | None = None,
    ) -> None:
        self.store = store
        self._runtime = runtime
        self._agent_manager = agent_manager

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------

    def _require_runtime(self) -> Any:
        """Return the attached AgentRuntime or raise."""
        if self._runtime is None:
            raise RuntimeError(
                "AgentRuntime is not attached to this service. "
                "Initialise with AgentService(store, runtime=runtime)."
            )
        return self._runtime

    def _require_agent_manager(self) -> Any:
        """Return the attached MultiAgentManager or raise."""
        if self._agent_manager is None:
            raise RuntimeError(
                "MultiAgentManager is not attached to this service. "
                "Initialise with AgentService(store, agent_manager=manager)."
            )
        return self._agent_manager

    # ------------------------------------------------------------------
    # Agent CRUD
    # ------------------------------------------------------------------

    async def create_agent(
        self,
        agent_id: str,
        model: dict[str, Any] | None = None,
    ) -> Any:
        """Create an Agent workspace and its ``agent.json`` configuration.

        A fresh workspace is created via the attached
        :class:`MultiAgentManager`; when *model* is supplied it overrides
        the default model section of :data:`DEFAULT_AGENT_JSON` and the
        resulting configuration is persisted to ``agent.json``.  The
        agent state is persisted so the agent survives restarts.

        Returns the created :class:`Workspace`.
        """
        manager = self._require_agent_manager()
        workspace = await manager.get_or_create_workspace(agent_id)

        # Write the agent configuration (model overrides merge over the
        # defaults; tools / settings keep their default shape).
        config: dict[str, Any] = {}
        if model:
            config["model"] = {
                k: v for k, v in model.items() if v not in (None, "")
            }
        if config:
            workspace.write_agent_config(config)

        # Persist the agent record (same shape as AgentInstance.to_state_dict
        # so AgentRuntime can restore it on startup).
        self.store.save_agent_state(
            agent_id,
            {
                "agent_id": agent_id,
                "state": "idle",
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "last_active_at": None,
                "error": None,
            },
        )
        return workspace

    async def start_agent(self, agent_id: str, **kwargs: Any) -> Any:
        """Start an agent from its workspace ``agent.json`` configuration.

        Loads (or creates) the workspace, reads ``agent.json``, and
        delegates to :meth:`AgentRuntime.start_agent`.  Returns the
        :class:`~agentcore.runtime.agent_runtime.AgentInstance`.
        """
        rt = self._require_runtime()
        manager = self._require_agent_manager()
        workspace = await manager.get_or_create_workspace(agent_id)
        agent_config = workspace.read_agent_config()
        return await rt.start_agent(
            agent_id,
            agent_config,
            workspace_dir=workspace.workspace_dir,
            workspace=workspace,
            **kwargs,
        )

    async def stop_agent(self, agent_id: str) -> None:
        """Stop a running agent."""
        rt = self._require_runtime()
        await rt.stop_agent(agent_id)

    def get_agent_status(self, agent_id: str) -> dict[str, Any] | None:
        """Return the status dict for *agent_id*, or ``None``."""
        rt = self._require_runtime()
        instance = rt.get_agent(agent_id)
        if instance is None:
            return None
        return instance.to_state_dict()

    def list_agents(self) -> list[dict[str, Any]]:
        """Return status dicts for all tracked agents."""
        rt = self._require_runtime()
        return [inst.to_state_dict() for inst in rt.list_agents()]
