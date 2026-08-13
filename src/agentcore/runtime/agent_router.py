"""Agent management router: CRUD and lifecycle endpoints for managed agents.

Exposes the :class:`~agentcore.runtime.multi_agent_manager.MultiAgentManager`
(owned by the application lifespan and mounted on ``app.state``) as an
HTTP API under ``/api/agents``.

Endpoints
---------
* ``GET    /api/agents``                                   — list agents
* ``POST   /api/agents``                                   — create agent (agent.json config)
* ``GET    /api/agents/{agent_id}``                        — agent detail / status
* ``POST   /api/agents/{agent_id}/start``                  — start agent
* ``POST   /api/agents/{agent_id}/stop``                   — stop agent
* ``POST   /api/agents/{agent_id}/reload``                 — hot-reload workspace
* ``DELETE /api/agents/{agent_id}``                        — delete agent
* ``GET    /api/agents/{agent_id}/kernel/{filename}``      — read kernel file
* ``PUT    /api/agents/{agent_id}/kernel/{filename}``      — write kernel file
* ``PUT    /api/agents/{agent_id}/model``                  — update model configuration
* ``GET    /api/agents/{agent_id}/sessions``               — list sessions
* ``GET    /api/agents/{agent_id}/sessions/{sid}/history`` — session history
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agentcore.runtime.agent_ids import known_agent_ids
from agentcore.runtime.chat_router import invalidate_agent_graph
from agentcore.runtime.workspace import KERNEL_FILE_NAMES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ModelConfigIn(BaseModel):
    """Optional model section for ``agent.json``."""

    provider: str | None = None
    name: str | None = None
    base_url: str | None = None


class CreateAgentRequest(BaseModel):
    """Payload for creating an agent.

    When *model* is omitted (or fully empty) the workspace's default
    ``agent.json`` model configuration is used.
    """

    agent_id: str
    model: ModelConfigIn | None = None


class StartAgentRequest(BaseModel):
    """Payload for starting an agent (agent_id is taken from the path)."""

    agent_id: str


class SendMessageRequest(BaseModel):
    """Payload for sending a message to an agent."""

    agent_id: str
    message: str


class KernelFileUpdate(BaseModel):
    """Payload for overwriting a kernel file's content."""

    content: str


class UpdateModelRequest(BaseModel):
    """Payload for ``PUT /api/agents/{agent_id}/model``.

    ``provider`` and ``name`` are required; ``base_url`` / ``api_key_env``
    are optional — when omitted the existing values in ``agent.json`` are
    kept.
    """

    provider: str
    name: str
    base_url: str | None = None
    api_key_env: str | None = None


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_manager(request: Request) -> Any:
    """Extract the :class:`MultiAgentManager` from ``request.app.state``."""
    manager = getattr(request.app.state, "agent_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="MultiAgentManager is not available.",
        )
    return manager


def _get_service(request: Request) -> Any:
    """Extract the :class:`AgentService` from ``request.app.state``."""
    service = getattr(request.app.state, "service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="AgentService is not available.",
        )
    return service


def _get_store(request: Request) -> Any:
    """Extract the :class:`ControlPlaneStore` from ``request.app.state``."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="ControlPlaneStore is not available.",
        )
    return store


def _key_error_detail(exc: KeyError) -> str:
    """Render a KeyError without the repr-style surrounding quotes."""
    return str(exc.args[0]) if exc.args else str(exc)


def _agent_runtime_state(request: Request, agent_id: str) -> str | None:
    """Return the runtime lifecycle state for *agent_id*, if tracked."""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        return None
    instance = runtime.get_agent(agent_id)
    if instance is None:
        return None
    return instance.state.value


def _known_agent_ids(request: Request) -> set[str]:
    """All agent ids known to the system (loaded, persisted, or tracked).

    Thin wrapper over the shared :func:`agentcore.runtime.agent_ids.known_agent_ids`.
    """
    return known_agent_ids(request.app.state)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_agents(request: Request) -> list[dict[str, Any]]:
    """List all agents (loaded workspaces plus persisted / tracked ones)."""
    manager = _get_manager(request)

    agents: list[dict[str, Any]] = []
    for agent_id in sorted(_known_agent_ids(request)):
        workspace = manager.get_workspace(agent_id)
        status = workspace.get_status() if workspace is not None else None
        agents.append(
            {
                "agent_id": agent_id,
                "state": _agent_runtime_state(request, agent_id),
                "loaded": workspace is not None,
                "session_count": status["session_count"] if status else 0,
            }
        )
    return agents


@router.post("")
async def create_agent(payload: CreateAgentRequest, request: Request) -> dict[str, Any]:
    """Create an agent (workspace + ``agent.json`` configuration)."""
    service = _get_service(request)

    if payload.agent_id in _known_agent_ids(request):
        raise HTTPException(
            status_code=409,
            detail=f"Agent {payload.agent_id!r} already exists",
        )

    model = payload.model.model_dump(exclude_none=True) if payload.model else None

    try:
        workspace = await service.create_agent(payload.agent_id, model=model or None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create agent %s", payload.agent_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create agent {payload.agent_id!r}: {exc}",
        ) from exc

    return {"result": "ok", "agent_id": payload.agent_id, "workspace": workspace.get_status()}


@router.get("/{agent_id}")
async def get_agent(agent_id: str, request: Request) -> dict[str, Any]:
    """Return the status of a single agent."""
    if agent_id not in _known_agent_ids(request):
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    manager = _get_manager(request)
    workspace = manager.get_workspace(agent_id)
    status = workspace.get_status() if workspace is not None else {}
    status["agent_id"] = agent_id
    status["state"] = _agent_runtime_state(request, agent_id)
    status["loaded"] = workspace is not None
    return status


@router.post("/{agent_id}/start")
async def start_agent(agent_id: str, request: Request) -> dict[str, Any]:
    """Start an agent from its workspace ``agent.json`` configuration."""
    if agent_id not in _known_agent_ids(request):
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    service = _get_service(request)
    try:
        instance = await service.start_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to start agent %s", agent_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start agent {agent_id!r}: {exc}",
        ) from exc

    return {"result": "ok", "agent_id": agent_id, "state": instance.state.value}


@router.post("/{agent_id}/stop")
async def stop_agent(agent_id: str, request: Request) -> dict[str, Any]:
    """Stop a running agent.

    Beyond the runtime state transition this also unloads the agent's
    workspace from memory (flushing to disk — files stay in place) and
    invalidates the cached agent graph, so a later chat rebuilds the
    agent from scratch (warm-up happens lazily on next use).
    """
    service = _get_service(request)
    try:
        await service.stop_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found") from exc
    except ValueError as exc:
        # Illegal lifecycle transition (e.g. already stopped).
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to stop agent %s", agent_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stop agent {agent_id!r}: {exc}",
        ) from exc

    # Unload the workspace from memory (best-effort; files are kept on
    # disk — rmtree only ever happens on DELETE agent).
    manager = getattr(request.app.state, "agent_manager", None)
    if manager is not None:
        try:
            await manager.unload_workspace(agent_id)
        except Exception:
            logger.exception(
                "Failed to unload workspace for agent %s", agent_id
            )

    # Drop the cached agent graph so the next start/chat rebuilds it.
    try:
        await invalidate_agent_graph(request.app.state, agent_id)
    except Exception:
        logger.exception(
            "Failed to invalidate agent graph on stop for %s", agent_id
        )

    return {"result": "ok", "agent_id": agent_id, "state": "stopped"}


@router.post("/{agent_id}/reload")
async def reload_agent(agent_id: str, request: Request) -> dict[str, str]:
    """Hot-reload the agent's workspace without affecting other agents."""
    manager = _get_manager(request)
    workspace = manager.get_workspace(agent_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    try:
        await manager.reload_workspace(agent_id)
    except Exception as exc:
        logger.exception("Failed to reload agent %s", agent_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reload agent {agent_id!r}: {exc}",
        ) from exc

    # Drop the cached agent graph (and checkpointed memory/skills state)
    # so the next chat recreates the agent with the freshly re-read
    # kernel files.
    await invalidate_agent_graph(request.app.state, agent_id)

    return {"status": "reloaded", "agent_id": agent_id}


@router.get("/{agent_id}/kernel/{filename}")
async def get_kernel_file(
    agent_id: str, filename: str, request: Request
) -> dict[str, Any]:
    """Read a kernel file (``agent.md`` / ``profile.md`` / ``soul.md``)."""
    if filename not in KERNEL_FILE_NAMES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown kernel file {filename!r}; expected one of "
            f"{list(KERNEL_FILE_NAMES)}",
        )

    manager = _get_manager(request)
    workspace = manager.get_workspace(agent_id)
    if workspace is None:
        if agent_id not in _known_agent_ids(request):
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
        workspace = await manager.get_or_create_workspace(agent_id)

    content = workspace.read_kernel_file(filename)
    if content is None:
        raise HTTPException(
            status_code=404,
            detail=f"Kernel file {filename!r} not found for agent {agent_id!r}",
        )
    return {"agent_id": agent_id, "filename": filename, "content": content}


@router.put("/{agent_id}/kernel/{filename}")
async def put_kernel_file(
    agent_id: str,
    filename: str,
    payload: KernelFileUpdate,
    request: Request,
) -> dict[str, Any]:
    """Overwrite a kernel file.

    The new content is written to disk (UTF-8) and picked up when the
    agent graph is next (re)created — the cached graph is invalidated
    immediately so the next chat uses the new system_prompt.
    """
    if filename not in KERNEL_FILE_NAMES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown kernel file {filename!r}; expected one of "
            f"{list(KERNEL_FILE_NAMES)}",
        )

    manager = _get_manager(request)
    workspace = manager.get_workspace(agent_id)
    if workspace is None:
        if agent_id not in _known_agent_ids(request):
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
        workspace = await manager.get_or_create_workspace(agent_id)

    try:
        workspace.write_kernel_file(filename, payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Force the agent to be rebuilt with the updated kernel files.
    await invalidate_agent_graph(request.app.state, agent_id)

    return {
        "result": "ok",
        "agent_id": agent_id,
        "filename": filename,
        "system_prompt_length": len(workspace.get_system_prompt()),
    }


@router.put("/{agent_id}/model")
async def put_agent_model(
    agent_id: str,
    payload: UpdateModelRequest,
    request: Request,
) -> dict[str, Any]:
    """Update the agent's model configuration (``agent.json`` → ``model``).

    ``provider`` and ``name`` are required; ``base_url`` / ``api_key_env``
    are optional and keep their existing values when omitted.  All other
    sections of ``agent.json`` (tools / settings / …) are preserved.
    The cached agent graph is invalidated so the next chat uses the new
    model.
    """
    manager = _get_manager(request)
    workspace = manager.get_workspace(agent_id)
    if workspace is None:
        if agent_id not in _known_agent_ids(request):
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
        workspace = await manager.get_or_create_workspace(agent_id)

    # Merge over the existing model section — unspecified optional fields
    # keep their current values; other agent.json sections are untouched.
    config = workspace.read_agent_config()
    model_section: dict[str, Any] = dict(config.get("model") or {})
    model_section["provider"] = payload.provider
    model_section["name"] = payload.name
    if payload.base_url is not None:
        model_section["base_url"] = payload.base_url
    if payload.api_key_env is not None:
        model_section["api_key_env"] = payload.api_key_env
    config["model"] = model_section

    try:
        workspace.write_agent_config(config)
    except Exception as exc:
        logger.exception("Failed to update model config for agent %s", agent_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update model config for {agent_id!r}: {exc}",
        ) from exc

    # Force the agent to be rebuilt with the new model on the next chat.
    await invalidate_agent_graph(request.app.state, agent_id)

    return {
        "result": "ok",
        "agent_id": agent_id,
        "model": model_section,
    }


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, request: Request) -> dict[str, str]:
    """Delete an agent: shut down its workspace and drop persisted state."""
    if agent_id not in _known_agent_ids(request):
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    manager = _get_manager(request)
    await manager.remove_workspace(agent_id)

    # 同时从 AgentRuntime 移除跟踪实例，避免列表接口继续返回该 agent。
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is not None:
        await runtime.remove_agent(agent_id)

    store = getattr(request.app.state, "store", None)
    if store is not None:
        store.agent_states.pop(agent_id, None)
        try:
            store._save_agents()
        except Exception:
            logger.exception("Failed to persist removal of agent %s", agent_id)

    return {"status": "deleted", "agent_id": agent_id}


@router.get("/{agent_id}/sessions")
async def list_sessions(agent_id: str, request: Request) -> dict[str, Any]:
    """List all chat sessions belonging to the agent's workspace."""
    manager = _get_manager(request)
    workspace = manager.get_workspace(agent_id)
    if not workspace:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return {"sessions": workspace.chat_manager.list_sessions()}


@router.get("/{agent_id}/sessions/{session_id}/history")
async def get_session_history(
    agent_id: str,
    session_id: str,
    request: Request,
    limit: int = 50,
) -> dict[str, Any]:
    """Return the most recent *limit* messages of a session."""
    manager = _get_manager(request)
    workspace = manager.get_workspace(agent_id)
    if not workspace:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    try:
        history = workspace.chat_manager.get_history(session_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    return {"messages": history}
