"""Agent runtime bookkeeper: lifecycle state tracking for deep agent instances.

This module provides :class:`AgentRuntime`, the control-plane bookkeeper
that records the *observed* lifecycle state of each managed agent.

**Important**: deepagents agents are **request-driven** — an agent is a
compiled LangGraph graph built lazily by
:func:`~agentcore.runtime.chat_router._resolve_agent_graph` on the first
chat request.  There is no resident process per agent, therefore this
class deliberately exposes **no** start/stop/pause control surface: it
only records facts about agents (existence and in-flight activity).

Lifecycle semantics
-------------------
* ``idle`` — the agent has **no in-flight request**; this is the normal
  resting state between requests, not an error or "not started"
  condition.
* ``busy`` — the agent is executing a request right now (a chat turn,
  a heartbeat beat or a scheduled cron run).  Every execution path
  (``chat_router`` / ``heartbeat`` / ``cron_manager``) marks the agent
  busy before invoking the graph and idle again when the turn ends.

States that existed in the legacy state machine (``running`` /
``paused`` / ``stopped`` / ``error``) had no behavioural counterpart in
a request-driven runtime and were removed; persisted legacy values are
mapped to ``idle`` on restore.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State model
# ---------------------------------------------------------------------------

class AgentState(str, Enum):
    """Observable lifecycle states of a managed agent.

    Only two states exist — both describe facts, not intentions:

    * ``IDLE``: no request in flight (the resting state).
    * ``BUSY``: an in-flight request (chat / heartbeat / cron turn).
    """

    IDLE = "idle"
    BUSY = "busy"


#: Legacy values persisted by the old (process-style) state machine.
#: They are mapped to the closest real state when restoring.
_LEGACY_STATE_MAP: dict[str, AgentState] = {
    "running": AgentState.IDLE,
    "paused": AgentState.IDLE,
    "stopped": AgentState.IDLE,
    "error": AgentState.IDLE,
}


def _coerce_state(raw: Any) -> AgentState:
    """Parse a persisted state value, mapping legacy values to ``idle``."""
    value = str(raw or "idle")
    try:
        return AgentState(value)
    except ValueError:
        return _LEGACY_STATE_MAP.get(value, AgentState.IDLE)


# ---------------------------------------------------------------------------
# AgentInstance
# ---------------------------------------------------------------------------

@dataclass
class AgentInstance:
    """Runtime representation of a single managed agent."""

    agent_id: str
    state: AgentState = AgentState.IDLE
    created_at: datetime = field(default_factory=datetime.now)
    last_active_at: datetime | None = None

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
            "error": None,  # legacy field kept for payload compatibility
        }

    @classmethod
    def from_state_dict(cls, data: dict[str, Any]) -> AgentInstance:
        """Reconstruct an :class:`AgentInstance` from a persisted dict.

        Legacy keys (``profile_id``, ``error``, legacy state values) are
        tolerated: unknown/legacy states map to :attr:`AgentState.IDLE`.
        """
        last_active = data.get("last_active_at")
        return cls(
            agent_id=data["agent_id"],
            state=_coerce_state(data.get("state")),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_active_at=datetime.fromisoformat(last_active) if last_active else None,
        )


# ---------------------------------------------------------------------------
# AgentRuntime
# ---------------------------------------------------------------------------

class AgentRuntime:
    """Bookkeeper for agent lifecycle facts.

    The runtime persists per-agent metadata through a
    :class:`~agentcore.repository.ControlPlaneStore` (or any object
    exposing ``save_agent_state`` / ``delete_agent_state`` /
    ``agent_states``) so the control plane survives restarts.

    Graph creation is **not** part of this class — graphs are built
    lazily per request by the chat router.  Typical usage::

        runtime = AgentRuntime(store)
        runtime.register_agent("agent-1")      # on creation
        await runtime.begin_turn("agent-1")    # before invoking the graph
        ...                                    # run the turn
        await runtime.end_turn("agent-1")      # after the turn settles

    Parameters
    ----------
    repository:
        A :class:`~agentcore.repository.ControlPlaneStore` (or any
        object exposing ``save_agent_state`` / ``load_agent_state`` /
        ``agent_states``).
    factory:
        Accepted for backwards compatibility with older constructor
        calls; ignored (graphs are created by the chat router).
    """

    def __init__(self, repository: Any, factory: Any = None) -> None:
        self._factory = factory  # legacy parameter — unused
        self._repository = repository
        self._instances: dict[str, AgentInstance] = {}
        self._lock = asyncio.Lock()

        # Attempt to restore previously persisted instances (metadata only;
        # nothing is "running" across restarts in a request-driven runtime).
        self._restore_from_persistence()

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    def _restore_from_persistence(self) -> None:
        """Load saved agent states from the repository into memory.

        Every restored agent lands in :attr:`AgentState.IDLE`: nothing
        can be in flight at process start, and legacy values such as
        ``running`` / ``stopped`` carry no meaning any more.
        """
        saved_states: dict[str, dict] = getattr(self._repository, "agent_states", {})
        for agent_id, state_dict in saved_states.items():
            try:
                instance = AgentInstance.from_state_dict(state_dict)
                instance.state = AgentState.IDLE
                self._instances[agent_id] = instance
            except Exception:
                logger.exception("Failed to restore agent %s — skipping", agent_id)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

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

    def _set_state(self, instance: AgentInstance, target: AgentState) -> None:
        """Move *instance* to *target* (idempotent), persist and log."""
        if instance.state == target:
            return
        old = instance.state
        instance.state = target
        instance.last_active_at = datetime.now()
        logger.info("Agent %s: %s → %s", instance.agent_id, old.value, target.value)
        self._persist(instance)

    # ------------------------------------------------------------------
    # Registration API (single write path for agent records)
    # ------------------------------------------------------------------

    def register_agent(self, agent_id: str) -> AgentInstance:
        """Ensure an :attr:`AgentState.IDLE` instance exists for *agent_id*.

        Idempotent: an existing instance is returned untouched.  This is
        the **only** place where new agent records are persisted, so the
        control plane has a single write path.
        """
        instance = self._instances.get(agent_id)
        if instance is not None:
            return instance
        instance = AgentInstance(agent_id=agent_id)
        self._instances[agent_id] = instance
        self._persist(instance)
        logger.info("Agent %s registered (state=idle)", agent_id)
        return instance

    async def remove_agent(self, agent_id: str) -> None:
        """Remove the agent instance from tracking and persistence.

        Persistence is always cleaned up (even when the agent was not
        tracked in *\_instances*) so that a restart cannot resurrect a
        deleted agent from stale repository state.
        """
        async with self._lock:
            self._instances.pop(agent_id, None)
            try:
                delete = getattr(self._repository, "delete_agent_state", None)
                if callable(delete):
                    delete(agent_id)
                else:
                    states = getattr(self._repository, "agent_states", None)
                    if states is not None:
                        states.pop(agent_id, None)
            except Exception:
                logger.exception(
                    "Failed to remove persisted state for agent %s", agent_id
                )
            logger.info("Removed agent %s", agent_id)

    # ------------------------------------------------------------------
    # Turn tracking (busy/idle)
    # ------------------------------------------------------------------

    async def start_agent(self, agent_id: str, **kwargs: Any) -> AgentInstance:
        """Ensure the agent is registered and idle (warm-up no-op).

        Retained for API compatibility: in a request-driven runtime
        there is nothing to "start" — graphs are built lazily on first
        use.  This method only guarantees a persisted record exists.
        Extra positional configuration / keyword arguments are accepted
        and ignored.
        """
        async with self._lock:
            return self.register_agent(agent_id)

    async def stop_agent(self, agent_id: str) -> None:
        """Mark the agent idle (idempotent).

        In a request-driven runtime "stop" has no process to terminate;
        actual resource release (workspace unload, graph cache drop) is
        performed by the caller.  Unknown agents are ignored so the
        operation stays idempotent.
        """
        async with self._lock:
            instance = self._instances.get(agent_id)
            if instance is None:
                return
            self._set_state(instance, AgentState.IDLE)

    async def begin_turn(self, agent_id: str) -> None:
        """Mark the agent busy because a request started executing.

        Best-effort and idempotent.  A deleted agent MUST NOT be silently
        re-registered here — that was the primary zombie-resurrection
        path (an in-flight request / heartbeat / cron firing after the
        agent was deleted would rewrite its record into ``agents.json``).
        Only agents already tracked in memory, or persisted in the
        repository (i.e. legitimately existing), get a state transition.
        """
        try:
            async with self._lock:
                instance = self._instances.get(agent_id)
                if instance is None:
                    # Only adopt agents that the repository still knows
                    # about (e.g. restored after a restart).  Anything
                    # else is treated as deleted / unknown and ignored.
                    persisted = getattr(
                        self._repository, "agent_states", {}
                    )
                    if agent_id not in persisted:
                        logger.warning(
                            "begin_turn(%s) skipped: agent is not tracked "
                            "and has no persisted state — likely deleted",
                            agent_id,
                        )
                        return
                    instance = self.register_agent(agent_id)
                self._set_state(instance, AgentState.BUSY)
        except Exception:
            logger.exception("Failed to mark agent %s busy", agent_id)

    async def end_turn(self, agent_id: str) -> None:
        """Mark the agent idle again after its request finished."""
        try:
            async with self._lock:
                instance = self._instances.get(agent_id)
                if instance is None:
                    return
                self._set_state(instance, AgentState.IDLE)
        except Exception:
            logger.exception("Failed to mark agent %s idle", agent_id)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get_agent(self, agent_id: str) -> AgentInstance | None:
        """Return the instance for *agent_id*, or ``None`` if not found."""
        return self._instances.get(agent_id)

    def list_agents(self) -> list[AgentInstance]:
        """Return all tracked agent instances."""
        return list(self._instances.values())


# ---------------------------------------------------------------------------
# App-state convenience helpers (best-effort, for execution paths)
# ---------------------------------------------------------------------------


async def mark_turn_begin(app_state: Any, agent_id: str) -> None:
    """Mark *agent_id* busy via the runtime bound to *app_state* (if any)."""
    runtime = getattr(app_state, "runtime", None)
    if runtime is not None:
        await runtime.begin_turn(agent_id)


async def mark_turn_end(app_state: Any, agent_id: str) -> None:
    """Mark *agent_id* idle via the runtime bound to *app_state* (if any)."""
    runtime = getattr(app_state, "runtime", None)
    if runtime is not None:
        await runtime.end_turn(agent_id)
