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

from agentcore.runtime.agent_ids import known_agent_ids as _shared_known_agent_ids

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats", tags=["stats"])

# 新状态模型下 state 只有 idle/busy（派生事实）；仪表盘计数以 workspace 是否装载为准，
# 不再依赖生命周期状态字段。


def _known_agent_ids(request: Request) -> set[str]:
    """All persisted agent ids (see :mod:`agentcore.runtime.agent_ids`)."""
    return _shared_known_agent_ids(request.app.state)


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
        if loaded:
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
