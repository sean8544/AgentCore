"""Agent statistics router.

Aggregates high-level runtime numbers — agent count, running agents,
session count and message count — for the dashboard.

Endpoints
---------
* ``GET /api/stats`` — aggregated statistics + per-agent breakdown
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats", tags=["stats"])

# Runtime states considered "running" for the dashboard counters.
_RUNNING_STATES = frozenset({"running", "idle", "started"})


def _known_agent_ids(request: Request) -> set[str]:
    """All agent ids known to the system (loaded, persisted, or tracked)."""
    ids: set[str] = set()
    manager = getattr(request.app.state, "agent_manager", None)
    if manager is not None:
        ids.update(manager.list_workspaces())
    store = getattr(request.app.state, "store", None)
    if store is not None:
        ids.update(getattr(store, "agent_states", {}).keys())
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is not None:
        ids.update(inst.agent_id for inst in runtime.list_agents())
    return ids


def _agent_runtime_state(request: Request, agent_id: str) -> str | None:
    """Return the runtime lifecycle state for *agent_id*, if tracked."""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        return None
    instance = runtime.get_agent(agent_id)
    if instance is None:
        return None
    return instance.state.value


@router.get("")
async def get_stats(request: Request) -> dict[str, Any]:
    """Return aggregated statistics for all agents and sessions."""
    manager = getattr(request.app.state, "agent_manager", None)
    store = getattr(request.app.state, "store", None)

    agent_ids = sorted(_known_agent_ids(request))

    # Sessions & messages (from the persisted control-plane store).
    sessions = store.load_sessions() if store is not None else {}
    total_messages = 0
    sessions_by_agent: dict[str, int] = {}
    messages_by_agent: dict[str, int] = {}
    for data in sessions.values():
        aid = data.get("agent_id", "")
        count = len(data.get("messages", []))
        total_messages += count
        sessions_by_agent[aid] = sessions_by_agent.get(aid, 0) + 1
        messages_by_agent[aid] = messages_by_agent.get(aid, 0) + count

    agents: list[dict[str, Any]] = []
    running_count = 0
    for agent_id in agent_ids:
        workspace = (
            manager.get_workspace(agent_id) if manager is not None else None
        )
        state = _agent_runtime_state(request, agent_id)
        loaded = workspace is not None
        if state in _RUNNING_STATES or loaded:
            running_count += 1
        agents.append(
            {
                "agent_id": agent_id,
                "state": state,
                "loaded": loaded,
                "session_count": sessions_by_agent.get(agent_id, 0),
                "message_count": messages_by_agent.get(agent_id, 0),
                "skills_count": workspace.count_skills() if workspace else 0,
            }
        )

    return {
        "agent_count": len(agent_ids),
        "running_count": running_count,
        "session_count": len(sessions),
        "message_count": total_messages,
        "agents": agents,
    }
