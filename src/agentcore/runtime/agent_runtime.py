"""Agent runtime manager: lifecycle management for deep agent instances.

This module provides :class:`AgentRuntime`, the central coordinator for
creating, controlling, and invoking running agent instances.  Each agent
is tracked via an :class:`AgentInstance` dataclass whose state transitions
are governed by a strict state machine.

Lifecycle semantics
-------------------
deepagents agents are **request-driven**: there is no resident background
loop per agent.  Every agent invocation executes a graph to completion
inside the serving request and returns; between requests nothing runs.

* ``idle``    — the agent has **no in-flight request**; this is the
  normal resting state, not an error or "not started" condition.
* ``start``   — *warm-up*: pre-build the agent graph / load the workspace
  into memory so the first real request is faster.
* ``stop``    — *unload*: release in-memory state (workspace + cached
  graph); on-disk files are preserved.  The agent is re-materialised
  lazily on the next use.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator

from langgraph.graph.state import CompiledStateGraph

from agentcore.runtime.agent_factory import AgentFactory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class AgentState(str, Enum):
    """Allowed lifecycle states for a managed agent instance."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


# Legal transitions: from_state → {allowed_target_states}
_VALID_TRANSITIONS: dict[AgentState, frozenset[AgentState]] = {
    AgentState.IDLE: frozenset({AgentState.RUNNING, AgentState.STOPPED, AgentState.ERROR}),
    AgentState.RUNNING: frozenset({AgentState.PAUSED, AgentState.STOPPED, AgentState.ERROR}),
    AgentState.PAUSED: frozenset({AgentState.RUNNING, AgentState.STOPPED, AgentState.ERROR}),
    AgentState.STOPPED: frozenset({AgentState.ERROR}),
    AgentState.ERROR: frozenset({AgentState.IDLE}),
}


def _validate_transition(current: AgentState, target: AgentState) -> None:
    """Raise if moving from *current* to *target* is not allowed."""
    if target not in _VALID_TRANSITIONS.get(current, frozenset()):
        raise ValueError(
            f"Illegal state transition: {current.value} → {target.value}"
        )


# ---------------------------------------------------------------------------
# AgentInstance
# ---------------------------------------------------------------------------

@dataclass
class AgentInstance:
    """Runtime representation of a single managed agent."""

    agent_id: str
    state: AgentState = AgentState.IDLE
    agent: CompiledStateGraph | None = field(default=None, repr=False)
    created_at: datetime = field(default_factory=datetime.now)
    last_active_at: datetime | None = None
    error: str | None = None

    # -- serialisation helpers (for persistence) ----------------------------

    def to_state_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of the instance metadata."""
        return {
            "agent_id": self.agent_id,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "last_active_at": (
                self.last_active_at.isoformat() if self.last_active_at else None
            ),
            "error": self.error,
        }

    @classmethod
    def from_state_dict(cls, data: dict[str, Any]) -> AgentInstance:
        """Reconstruct an :class:`AgentInstance` from a persisted dict.

        The ``agent`` handle is *not* restored — it must be recreated via
        :class:`AgentFactory` after loading.  Legacy keys (e.g. the
        removed ``profile_id``) are silently ignored.
        """
        last_active = data.get("last_active_at")
        return cls(
            agent_id=data["agent_id"],
            state=AgentState(data.get("state", "idle")),
            agent=None,
            created_at=datetime.fromisoformat(data["created_at"]),
            last_active_at=datetime.fromisoformat(last_active) if last_active else None,
            error=data.get("error"),
        )


# ---------------------------------------------------------------------------
# AgentRuntime
# ---------------------------------------------------------------------------

class AgentRuntime:
    """Manage the full lifecycle of deep agent instances.

    The runtime wraps an :class:`AgentFactory` (for creating agent graphs)
    and a :class:`~agentcore.repository.ControlPlaneStore` (for persisting
    agent state across restarts).

    Typical usage::

        runtime = AgentRuntime(factory, store)
        instance = await runtime.start_agent("agent-1", agent_config)
        result = await runtime.invoke("agent-1", {"messages": "Hello"})
        await runtime.stop_agent("agent-1")
    """

    def __init__(self, factory: AgentFactory, repository: Any) -> None:
        """Initialise the runtime manager.

        Parameters
        ----------
        factory:
            An :class:`AgentFactory` used to create deep agent graphs.
        repository:
            A :class:`~agentcore.repository.ControlPlaneStore` (or any
            object exposing ``save_agent_state`` / ``load_agent_state`` /
            ``agent_states``).
        """
        self._factory = factory
        self._repository = repository
        self._instances: dict[str, AgentInstance] = {}
        self._lock = asyncio.Lock()

        # Attempt to restore previously persisted instances (metadata only;
        # the actual agent graphs must be recreated on demand).
        self._restore_from_persistence()

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    def _restore_from_persistence(self) -> None:
        """Load saved agent states from the repository into memory."""
        saved_states: dict[str, dict] = getattr(self._repository, "agent_states", {})
        for agent_id, state_dict in saved_states.items():
            try:
                instance = AgentInstance.from_state_dict(state_dict)
                # Restored agents land in IDLE — their graph handle is gone
                # and they need to be (re)started explicitly.
                if instance.state not in (AgentState.STOPPED, AgentState.ERROR):
                    logger.info(
                        "Restoring agent %s (was %s) → idle",
                        agent_id,
                        instance.state.value,
                    )
                    instance.state = AgentState.IDLE
                else:
                    logger.info("Restoring agent %s in terminal state %s", agent_id, instance.state.value)
                self._instances[agent_id] = instance
            except Exception:
                logger.exception("Failed to restore agent %s — skipping", agent_id)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _transition(self, instance: AgentInstance, target: AgentState, *, error: str | None = None) -> None:
        """Move *instance* to *target* state, persist, and log."""
        _validate_transition(instance.state, target)
        old = instance.state
        instance.state = target
        instance.error = error
        logger.info("Agent %s: %s → %s", instance.agent_id, old.value, target.value)
        self._persist(instance)

    def _persist(self, instance: AgentInstance) -> None:
        """Flush the instance metadata to the repository."""
        try:
            self._repository.save_agent_state(instance.agent_id, instance.to_state_dict())
        except Exception:
            logger.exception("Failed to persist agent %s state", instance.agent_id)

    def _get_or_raise(self, agent_id: str) -> AgentInstance:
        """Return the instance for *agent_id* or raise ``KeyError``."""
        instance = self._instances.get(agent_id)
        if instance is None:
            raise KeyError(f"No agent instance with id {agent_id!r}")
        return instance

    # ------------------------------------------------------------------
    # Lifecycle API
    # ------------------------------------------------------------------

    async def start_agent(
        self,
        agent_id: str,
        agent_config: dict[str, Any],
        **kwargs: Any,
    ) -> AgentInstance:
        """Create and start an agent instance.

        If an instance with the same *agent_id* already exists in
        :attr:`AgentState.IDLE` or :attr:`AgentState.ERROR` state it will
        be restarted (a new agent graph is created).  If it is already
        running or paused, the existing instance is returned as-is.

        Parameters
        ----------
        agent_id:
            Unique identifier for this agent instance.
        agent_config:
            The agent configuration dict (from the workspace's
            ``agent.json``) used when creating the agent graph.
        **kwargs:
            Extra keyword arguments forwarded to
            :meth:`AgentFactory.create_agent`.

        Returns
        -------
        AgentInstance
            The newly started (or existing active) agent instance.
        """
        async with self._lock:
            existing = self._instances.get(agent_id)

            # If there is an active instance, return it directly.
            if existing is not None and existing.state in (
                AgentState.RUNNING,
                AgentState.PAUSED,
            ):
                logger.warning(
                    "Agent %s already active (state=%s); returning existing instance",
                    agent_id,
                    existing.state.value,
                )
                return existing

            # Create the deep agent graph via the factory.
            try:
                agent_graph = self._factory.create_agent(agent_config, **kwargs)
            except Exception as exc:
                logger.exception("Failed to create agent %s", agent_id)
                # If there is an existing (idle/error) instance, mark it error.
                if existing is not None:
                    self._transition(existing, AgentState.ERROR, error=str(exc))
                raise

            if existing is not None:
                # Reuse the metadata object (idle / error → running).
                existing.agent = agent_graph
                existing.last_active_at = datetime.now()
                self._transition(existing, AgentState.RUNNING)
                return existing

            # Brand-new instance.
            instance = AgentInstance(
                agent_id=agent_id,
                state=AgentState.RUNNING,
                agent=agent_graph,
                last_active_at=datetime.now(),
            )
            self._instances[agent_id] = instance
            self._persist(instance)
            logger.info("Agent %s started", agent_id)
            return instance

    async def stop_agent(self, agent_id: str) -> None:
        """Stop a running or paused agent instance.

        The agent graph reference is cleared so resources can be freed.
        """
        async with self._lock:
            instance = self._get_or_raise(agent_id)
            self._transition(instance, AgentState.STOPPED)
            instance.agent = None
            logger.info("Agent %s stopped", agent_id)

    async def remove_agent(self, agent_id: str) -> None:
        """移除 agent 实例（从跟踪列表中删除）。

        与 :meth:`stop_agent` 不同，此方法会将实例彻底从内存跟踪表
        中移除，并尽力清理 repository 中的持久化状态。
        """
        async with self._lock:
            if agent_id in self._instances:
                self._instances.pop(agent_id)
                # 清理持久化状态（repository 可能未提供专用删除方法，
                # 回退到直接从 agent_states 中弹出）。
                try:
                    delete = getattr(self._repository, "delete_agent_state", None)
                    if callable(delete):
                        delete(agent_id)
                    else:
                        states = getattr(self._repository, "agent_states", None)
                        if states is not None:
                            states.pop(agent_id, None)
                            save = getattr(self._repository, "_save_agents", None)
                            if callable(save):
                                save()
                except Exception:
                    logger.exception(
                        "Failed to remove persisted state for agent %s", agent_id
                    )
                logger.info("Removed agent %s", agent_id)

    async def pause_agent(self, agent_id: str) -> None:
        """Pause a running agent instance."""
        async with self._lock:
            instance = self._get_or_raise(agent_id)
            self._transition(instance, AgentState.PAUSED)
            logger.info("Agent %s paused", agent_id)

    async def resume_agent(self, agent_id: str) -> None:
        """Resume a paused agent instance."""
        async with self._lock:
            instance = self._get_or_raise(agent_id)
            if instance.agent is None:
                raise RuntimeError(
                    f"Agent {agent_id!r} has no graph handle; call start_agent() first"
                )
            self._transition(instance, AgentState.RUNNING)
            logger.info("Agent %s resumed", agent_id)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get_agent(self, agent_id: str) -> AgentInstance | None:
        """Return the instance for *agent_id*, or ``None`` if not found."""
        return self._instances.get(agent_id)

    def list_agents(self) -> list[AgentInstance]:
        """Return all tracked agent instances."""
        return list(self._instances.values())

    # ------------------------------------------------------------------
    # Invocation API
    # ------------------------------------------------------------------

    async def invoke(self, agent_id: str, message: str, **kwargs: Any) -> Any:
        """Synchronously invoke an agent (runs the full graph to completion).

        Parameters
        ----------
        agent_id:
            The agent to invoke.  Must be in :attr:`AgentState.RUNNING`.
        message:
            The user message string.
        **kwargs:
            Extra keyword arguments forwarded to the agent's ``invoke``
            method (e.g. ``config``, ``recursion_limit``).

        Returns
        -------
        Any
            The agent graph output.
        """
        async with self._lock:
            instance = self._get_or_raise(agent_id)
            if instance.state != AgentState.RUNNING:
                raise RuntimeError(
                    f"Agent {agent_id!r} is not running (state={instance.state.value})"
                )
            if instance.agent is None:
                raise RuntimeError(f"Agent {agent_id!r} has no graph handle")

            agent_graph = instance.agent

        # Execute outside the lock to allow concurrent state operations.
        try:
            input_data = {"messages": message}
            result = await agent_graph.ainvoke(input_data, **kwargs)
        except Exception as exc:
            async with self._lock:
                self._transition(instance, AgentState.ERROR, error=str(exc))
            raise

        async with self._lock:
            instance.last_active_at = datetime.now()
            self._persist(instance)

        return result

    async def stream(
        self, agent_id: str, message: str, **kwargs: Any
    ) -> AsyncIterator[Any]:
        """Asynchronously stream agent output chunks.

        Yields each chunk produced by the agent graph's ``astream`` method.
        The instance's ``last_active_at`` is updated after the stream
        completes.

        Parameters
        ----------
        agent_id:
            The agent to invoke.  Must be in :attr:`AgentState.RUNNING`.
        message:
            The user message string.
        **kwargs:
            Extra keyword arguments forwarded to the agent's ``astream``
            method.

        Yields
        ------
        Any
            Individual output chunks from the agent graph.
        """
        async with self._lock:
            instance = self._get_or_raise(agent_id)
            if instance.state != AgentState.RUNNING:
                raise RuntimeError(
                    f"Agent {agent_id!r} is not running (state={instance.state.value})"
                )
            if instance.agent is None:
                raise RuntimeError(f"Agent {agent_id!r} has no graph handle")

            agent_graph = instance.agent

        # Stream outside the lock.
        try:
            input_data = {"messages": message}
            async for chunk in agent_graph.astream(input_data, **kwargs):
                yield chunk
        except Exception as exc:
            async with self._lock:
                self._transition(instance, AgentState.ERROR, error=str(exc))
            raise

        async with self._lock:
            instance.last_active_at = datetime.now()
            self._persist(instance)
