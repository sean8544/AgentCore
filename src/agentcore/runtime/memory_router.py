# -*- coding: utf-8 -*-
"""Memory management router (/api/agents/{id}/memory).

Exposes the agent's file-based long-term memory (see
:mod:`agentcore.runtime.memory_consolidation` and the workspace's
``memory/`` directory):

* ``memory/MEMORY.md`` / ``memory/USER.md`` — long-term memory files,
  injected into the system prompt by the SDK's ``MemoryMiddleware``.
* ``memory/sessions/*.md`` — daily session archives written by the chat
  router; consumed by the background consolidation.

Endpoints
---------
* ``GET  /api/agents/{agent_id}/memory``                — full overview
* ``PUT  /api/agents/{agent_id}/memory/config``         — save config
* ``PUT  /api/agents/{agent_id}/memory/files/{name}``   — edit a memory file
* ``GET  /api/agents/{agent_id}/memory/archives/{name}``— read one archive
* ``POST /api/agents/{agent_id}/memory/consolidate``    — trigger a run now

Note: edited memory files become effective in **new** sessions — the
SDK loads ``memory=[]`` sources once per thread (checkpointed
``memory_contents``), so already-running threads keep their snapshot.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agentcore.runtime.agent_ids import known_agent_ids
from agentcore.runtime.cron_manager import CronManager
from agentcore.runtime.memory_consolidation import (
    ARCHIVED_DIR_NAME,
    consolidation_job_id,
    get_memory_config,
    list_session_archives,
    normalize_memory_config,
    read_consolidation_state,
    run_consolidation_once,
    save_memory_config,
    sync_memory_job,
)
from agentcore.runtime.workspace import MEMORY_FILE_NAME, USER_FILE_NAME

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["memory"])

#: Only the two main memory files are editable through this API.
_EDITABLE_FILES = {MEMORY_FILE_NAME, USER_FILE_NAME}

#: Daily archive file name validation (also prevents path traversal).
_ARCHIVE_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

#: In-flight manual consolidation tasks (kept referenced so the event
#: loop never garbage-collects them before completion).
_RUNNING_CONSOLIDATIONS: set[asyncio.Task] = set()


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

    Same lazy pattern as the heartbeat router so the API also works with
    synchronous test clients (no lifespan).
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


def _read_memory_file(workspace: Any, filename: str) -> dict[str, Any] | None:
    content = workspace.read_memory_file(filename)
    if content is None:
        return None
    return {"name": filename, "content": content, "size_bytes": len(content.encode("utf-8"))}


def _memory_payload(
    workspace: Any, cron_manager: CronManager, agent_id: str
) -> dict[str, Any]:
    """Assemble the GET response for one agent."""
    config = get_memory_config(workspace)
    last_run = None
    try:
        raw = workspace.read_agent_config().get("memory")
        if isinstance(raw, dict) and isinstance(raw.get("last_run"), dict):
            last_run = raw["last_run"]
    except Exception:
        pass

    job_id = consolidation_job_id(agent_id)
    state = cron_manager.get_internal_state(job_id)

    files = {
        name: _read_memory_file(workspace, name)
        for name in (MEMORY_FILE_NAME, USER_FILE_NAME)
    }

    return {
        "agent_id": agent_id,
        "config": config,
        "files": files,
        "archives": list_session_archives(workspace),
        "archived": list_session_archives(workspace, archived=True),
        "consolidation": {
            "state": read_consolidation_state(workspace),
            "scheduled": cron_manager.has_internal_job(job_id),
            "next_run_at": (
                state.next_run_at.isoformat() if state.next_run_at else None
            ),
            "last_status": state.last_status,
        },
        "last_run": last_run,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/{agent_id}/memory")
async def get_memory(agent_id: str, request: Request) -> dict[str, Any]:
    """Read the agent's memory files, archives and consolidation status."""
    workspace = await _get_workspace(request, agent_id)
    return _memory_payload(workspace, _get_cron_manager(request), agent_id)


@router.put("/{agent_id}/memory/config")
async def update_memory_config(
    agent_id: str, body: dict[str, Any], request: Request
) -> dict[str, Any]:
    """Save the memory configuration and hot-update the scheduler."""
    workspace = await _get_workspace(request, agent_id)

    try:
        config = normalize_memory_config(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    save_memory_config(workspace, config)
    logger.info(
        "Memory config saved for agent %s: enabled=%s cron=%s",
        agent_id,
        config["enabled"],
        config["consolidate_cron"],
    )

    cron_manager = _get_cron_manager(request)
    await sync_memory_job(cron_manager, request.app.state, agent_id)

    return _memory_payload(workspace, cron_manager, agent_id)


@router.put("/{agent_id}/memory/files/{filename}")
async def update_memory_file(
    agent_id: str, filename: str, body: dict[str, Any], request: Request
) -> dict[str, Any]:
    """Overwrite MEMORY.md or USER.md with user-supplied content.

    Only the two main memory files are writable through this endpoint;
    session archives are managed by the chat router / consolidation.
    """
    if filename not in _EDITABLE_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"File {filename!r} is not editable — allowed: "
            f"{sorted(_EDITABLE_FILES)}",
        )
    content = body.get("content")
    if not isinstance(content, str):
        raise HTTPException(
            status_code=400, detail="'content' must be a string"
        )

    workspace = await _get_workspace(request, agent_id)
    path = workspace.get_memory_dir() / filename
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to write {filename}: {exc}"
        ) from exc

    logger.info("Memory file %s updated for agent %s", filename, agent_id)
    return {
        "saved": True,
        "file": filename,
        # The SDK loads memory sources once per thread — new content is
        # injected into the system prompt of NEW sessions only.
        "note": "Effective in new sessions",
    }


@router.get("/{agent_id}/memory/archives/{name}")
async def get_archive_content(
    agent_id: str,
    name: str,
    request: Request,
    archived: bool = False,
) -> dict[str, Any]:
    """Read one daily session archive file (active or archived)."""
    if not _ARCHIVE_NAME_RE.match(name):
        raise HTTPException(
            status_code=400, detail="Archive name must look like 2026-01-31.md"
        )
    workspace = await _get_workspace(request, agent_id)
    base = workspace.get_sessions_archive_dir()
    path = (base / ARCHIVED_DIR_NAME / name) if archived else (base / name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Archive {name!r} not found")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to read archive: {exc}"
        ) from exc
    return {"name": name, "archived": archived, "content": content}


@router.post("/{agent_id}/memory/consolidate")
async def trigger_consolidation(
    agent_id: str, request: Request
) -> dict[str, Any]:
    """Trigger one consolidation run in the background (forced).

    Returns immediately; progress and the run record are visible through
    ``GET /api/agents/{agent_id}/memory`` (``last_run``).  One concurrent
    run per agent is allowed — extra triggers while a run is in flight
    are rejected.
    """
    workspace = await _get_workspace(request, agent_id)  # 404 for unknown

    in_flight = any(
        not task.done() and getattr(task, "_agentcore_agent_id", None) == agent_id
        for task in _RUNNING_CONSOLIDATIONS
    )
    if in_flight:
        raise HTTPException(
            status_code=409, detail="A consolidation run is already in progress"
        )

    app_state = request.app.state
    task = asyncio.create_task(
        run_consolidation_once(app_state, agent_id, force=True),
        name=f"memory-consolidation-{agent_id}",
    )
    task._agentcore_agent_id = agent_id  # type: ignore[attr-defined]
    _RUNNING_CONSOLIDATIONS.add(task)
    task.add_done_callback(_RUNNING_CONSOLIDATIONS.discard)

    logger.info("Manual memory consolidation started for agent %s", agent_id)
    return {
        "status": "started",
        "session_id": f"memory-consolidation-{agent_id}",
        "hint": f"Poll GET /api/agents/{agent_id}/memory for the run record",
    }
