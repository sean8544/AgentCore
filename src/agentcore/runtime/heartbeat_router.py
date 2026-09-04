# -*- coding: utf-8 -*-
"""Heartbeat configuration router (/api/agents/{id}/heartbeat).

The heartbeat periodically feeds the workspace's ``HEARTBEAT.md``
checklist to the agent (see :mod:`agentcore.runtime.heartbeat`).  The
configuration is stored in the per-agent ``agent.json`` under the
top-level ``heartbeat`` section::

    {
        "enabled": false,
        "every": "30m",
        "timeout_seconds": 600,
        "active_hours": {"start": "08:00", "end": "22:00"},
        "last_run": {"status": "success", "at": "...", "summary": "..."}
    }

Scheduling is delegated to the :class:`CronManager` as an *internal*
job (``_internal:heartbeat:{agent_id}``) — the same scheduler that
drives user cron jobs, so heartbeats benefit from its misfire grace,
keepalive loop and execution history without polluting the user-facing
job list.

Endpoints
---------
* ``GET  /api/agents/{agent_id}/heartbeat``      — current configuration
* ``PUT  /api/agents/{agent_id}/heartbeat``      — save configuration
* ``POST /api/agents/{agent_id}/heartbeat/run``  — trigger one run now
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agentcore.runtime.agent_ids import known_agent_ids
from agentcore.runtime.cron_manager import CronManager
from agentcore.runtime.heartbeat import (
    get_heartbeat_config,
    heartbeat_job_id,
    normalize_heartbeat_config,
    run_heartbeat_once,
    save_heartbeat_config,
    sync_heartbeat_job,
)
from agentcore.runtime.workspace import HEARTBEAT_MD_NAME

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["heartbeat"])


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_manager(request: Request) -> Any:
    manager = getattr(request.app.state, "agent_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="MultiAgentManager is not available.",
        )
    return manager


def _get_cron_manager(request: Request) -> CronManager:
    """Resolve the app-scoped CronManager, creating it when unstarted.

    Created lazily (not started) so the router also works without
    lifespan (e.g. synchronous test clients) — registrations land in the
    internal registry and attach to APScheduler once ``start()`` runs.
    """
    cron_manager = getattr(request.app.state, "cron_manager", None)
    if cron_manager is None:
        cron_manager = CronManager(request.app.state)
        request.app.state.cron_manager = cron_manager
    return cron_manager


async def _get_workspace(request: Request, agent_id: str) -> Any:
    """Resolve an existing agent's workspace (404 for unknown agents)."""
    if agent_id not in known_agent_ids(request.app.state):
        raise HTTPException(
            status_code=404, detail=f"Agent {agent_id!r} not found"
        )
    return await _get_manager(request).get_or_create_workspace(agent_id)


def _heartbeat_payload(
    workspace: Any, cron_manager: CronManager, agent_id: str
) -> dict[str, Any]:
    """Assemble the GET response for one agent."""
    config = get_heartbeat_config(workspace)
    last_run = None
    try:
        raw = workspace.read_agent_config().get("heartbeat")
        if isinstance(raw, dict) and isinstance(raw.get("last_run"), dict):
            last_run = raw["last_run"]
    except Exception:
        pass
    heartbeat_path = workspace.workspace_dir / HEARTBEAT_MD_NAME

    job_id = heartbeat_job_id(agent_id)
    state = cron_manager.get_internal_state(job_id)
    return {
        "agent_id": agent_id,
        "config": config,
        "query_file_exists": heartbeat_path.is_file(),
        "scheduled": cron_manager.has_internal_job(job_id),
        "next_run_at": (
            state.next_run_at.isoformat() if state.next_run_at else None
        ),
        "last_status": state.last_status,
        "last_run": last_run,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/{agent_id}/heartbeat")
async def get_heartbeat(agent_id: str, request: Request) -> dict[str, Any]:
    """Read the agent's heartbeat configuration and status."""
    workspace = await _get_workspace(request, agent_id)
    return _heartbeat_payload(workspace, _get_cron_manager(request), agent_id)


@router.put("/{agent_id}/heartbeat")
async def update_heartbeat(
    agent_id: str, body: dict[str, Any], request: Request
) -> dict[str, Any]:
    """Save the heartbeat configuration and hot-update the scheduler."""
    workspace = await _get_workspace(request, agent_id)

    try:
        config = normalize_heartbeat_config(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    save_heartbeat_config(workspace, config)
    logger.info(
        "Heartbeat config saved for agent %s: enabled=%s every=%s",
        agent_id,
        config["enabled"],
        config["every"],
    )

    # Apply immediately — register / replace / remove the internal job.
    cron_manager = _get_cron_manager(request)
    await sync_heartbeat_job(cron_manager, request.app.state, agent_id)

    return _heartbeat_payload(workspace, cron_manager, agent_id)


@router.post("/{agent_id}/heartbeat/run")
async def run_heartbeat(agent_id: str, request: Request) -> dict[str, Any]:
    """Trigger one heartbeat run right away and return the run record.

    Active hours are NOT enforced for manual runs — the user explicitly
    asked for it.  A missing ``HEARTBEAT.md`` still skips the run.
    """
    workspace = await _get_workspace(request, agent_id)

    record = await run_heartbeat_once(
        request.app.state, agent_id, check_active_hours=False
    )

    # Persist the run record alongside the configuration (manual runs
    # should be visible after a page reload too).
    if record.get("status") != "skipped":
        try:
            save_heartbeat_config(
                workspace,
                get_heartbeat_config(workspace),
                last_run=record,
            )
        except Exception:
            logger.exception(
                "Failed to persist heartbeat last_run for %s", agent_id
            )

    return record
