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

from agentcore.runtime import paths

from agentcore.runtime.agent_ids import known_agent_ids
from agentcore.runtime.chat_router import invalidate_agent_graph
from agentcore.runtime.heartbeat import heartbeat_job_id
from agentcore.runtime.memory_consolidation import consolidation_job_id
from agentcore.runtime.multi_agent_manager import _rmtree_with_retry
from agentcore.runtime.workspace import ALL_KERNEL_FILE_NAMES

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

    Optional *description*, *system_prompt*, *enable_subagents*, and
    *inherit_parent_tools* are written into the ``settings`` section
    of the new agent's ``agent.json`` so the subagent registry and
    the agent factory can pick them up at graph-build time.

    *settings* allows passing arbitrary settings (e.g. ``backend`` for
    sandbox configuration) that are merged into the ``settings`` section.
    """

    agent_id: str
    model: ModelConfigIn | None = None
    description: str | None = None
    system_prompt: str | None = None
    enable_subagents: bool | None = None
    inherit_parent_tools: bool | None = None
    interrupt_rules: list[dict[str, Any]] | None = None
    settings: dict[str, Any] | None = None


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
    / ``api_key`` / ``headers`` / ``extra_body`` are optional — when omitted
    the existing values in ``agent.json`` are kept.
    """

    provider: str
    name: str
    base_url: str | None = None
    api_key_env: str | None = None
    api_key: str | None = None
    headers: list[dict[str, str]] | None = None
    extra_body: dict[str, Any] | None = None


class UpdateSettingsRequest(BaseModel):
    """Payload for ``PUT /api/agents/{agent_id}/settings``.

    All fields are optional; only the provided ones are updated and the
    rest of ``agent.json`` is preserved.
    """

    description: str | None = None
    system_prompt: str | None = None
    enable_subagents: bool | None = None
    inherit_parent_tools: bool | None = None
    enable_planning: bool | None = None
    enable_a2ui: bool | None = None
    interrupt_rules: list[dict[str, Any]] | None = None
    subagent_ids: list[str] | None = None
    model_id: str | None = None


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
    """List all agents (only those persisted in ``agents.json``).

    As a self-healing measure we also unload any in-memory workspace that
    no longer has a persisted record — such zombies used to leak back
    into the UI because :func:`known_agent_ids` used to trust
    ``manager.list_workspaces()``.  Dropping them here guarantees the
    agents list, the sessions filter and the heartbeat scheduler all
    converge on the same authoritative view.
    """
    manager = _get_manager(request)

    persisted = _known_agent_ids(request)
    for stale in list(manager.list_workspaces()):
        if stale not in persisted:
            try:
                await manager.unload_workspace(stale)
                logger.info(
                    "Auto-unloaded zombie workspace with no persisted record: %s",
                    stale,
                )
            except Exception:
                logger.exception(
                    "Failed to auto-unload zombie workspace %s", stale
                )

    agents: list[dict[str, Any]] = []
    for agent_id in sorted(persisted):
        workspace = manager.get_workspace(agent_id)
        status = workspace.get_status() if workspace is not None else None

        # Read settings for description, enable_subagents and backend_type.
        description = ""
        enable_subagents = False
        backend_type = "local"
        if workspace is not None:
            try:
                agent_config = workspace.read_agent_config()
                settings = agent_config.get("settings", {}) or {}
                description = settings.get("description", "") or ""
                enable_subagents = bool(settings.get("enable_subagents", False))
                backend_cfg = settings.get("backend", {}) or {}
                backend_type = backend_cfg.get("type", "local") or "local"
            except Exception:
                pass

        agents.append(
            {
                "agent_id": agent_id,
                "state": _agent_runtime_state(request, agent_id),
                "loaded": workspace is not None,
                "session_count": status["session_count"] if status else 0,
                "description": description,
                "enable_subagents": enable_subagents,
                "backend_type": backend_type,
                "subagents": _subagent_summaries(request, agent_id) if enable_subagents else [],
            }
        )
    return agents


def _subagent_summaries(request: Request, agent_id: str) -> list[dict[str, str]]:
    """Return lightweight subagent specs (name + description) for *agent_id*.

    Used to surface the parent → subagent topology in the console.  Only
    loaded workspaces participate (spec building reads workspace files).

    The result honours the agent's own ``settings.subagent_ids``
    whitelist so the console topology matches exactly what the runtime
    injects at graph-build time (see
    :func:`agentcore.runtime.chat_router._resolve_agent_graph`).
    """
    manager = getattr(request.app.state, "agent_manager", None)
    if manager is None:
        return []
    # Read this agent's delegation whitelist (None/empty = all agents).
    whitelist: list[str] | None = None
    workspace = manager.get_workspace(agent_id)
    if workspace is not None:
        try:
            settings = workspace.read_agent_config().get("settings", {}) or {}
            whitelist = settings.get("subagent_ids") or None
        except Exception:
            whitelist = None
    try:
        from agentcore.runtime.subagent_registry import SubAgentRegistry

        specs = SubAgentRegistry(manager).get_subagents(
            exclude_agent_id=agent_id,
            whitelist=whitelist,
        )
        return [
            {"name": spec.get("name", ""), "description": spec.get("description", "")}
            for spec in specs
        ]
    except Exception:
        logger.exception("Failed to build subagent topology for %s", agent_id)
        return []


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
        workspace = await service.create_agent(
            payload.agent_id,
            model=model or None,
            description=payload.description,
            system_prompt=payload.system_prompt,
            enable_subagents=payload.enable_subagents,
            inherit_parent_tools=payload.inherit_parent_tools,
            interrupt_rules=payload.interrupt_rules,
            extra_settings=payload.settings,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create agent %s", payload.agent_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create agent {payload.agent_id!r}: {exc}",
        ) from exc

    # Dynamic discovery (Task 2.3): invalidate subagent consumers so they
    # pick up the new agent on next graph build.
    await _invalidate_subagent_consumers(request)

    return {"result": "ok", "agent_id": payload.agent_id, "workspace": workspace.get_status()}


async def _invalidate_subagent_consumers(request: Request) -> None:
    """Invalidate graphs for all agents with ``enable_subagents=true``.

    Called after agent creation / reload / deletion so that any agent
    that aggregates subagents picks up the new topology on its next
    chat (dynamic discovery — Task 2.3).
    """
    manager = getattr(request.app.state, "agent_manager", None)
    if manager is None:
        return
    workspaces: dict[str, Any] = getattr(manager, "_workspaces", {})
    for agent_id, ws in workspaces.items():
        try:
            config = ws.read_agent_config()
        except Exception:
            continue
        settings = config.get("settings", {}) or {}
        if settings.get("enable_subagents"):
            try:
                await invalidate_agent_graph(request.app.state, agent_id)
            except Exception:
                logger.exception(
                    "Failed to invalidate graph for subagent consumer %s",
                    agent_id,
                )


@router.get("/{agent_id}")
async def get_agent(agent_id: str, request: Request) -> dict[str, Any]:
    """Return the status of a single agent."""
    if agent_id not in _known_agent_ids(request):
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    manager = _get_manager(request)
    workspace = manager.get_workspace(agent_id)
    status = workspace.get_status() if workspace is not None else {}
    settings: dict[str, Any] = {}
    if workspace is not None:
        try:
            agent_config = workspace.read_agent_config()
            settings = agent_config.get("settings", {}) or {}
        except Exception:
            logger.exception("Failed to read settings for agent %s", agent_id)
    status["agent_id"] = agent_id
    status["state"] = _agent_runtime_state(request, agent_id)
    status["loaded"] = workspace is not None
    status["settings"] = settings
    status["subagents"] = _subagent_summaries(request, agent_id) if settings.get("enable_subagents") else []
    # Include model info for the config page
    if workspace is not None:
        try:
            agent_config = workspace.read_agent_config()
            status["model"] = agent_config.get("model") or {}
        except Exception:
            status["model"] = {}
    return status


@router.post("/{agent_id}/start")
async def start_agent(agent_id: str, request: Request) -> dict[str, Any]:
    """Warm up an agent: load its workspace and ensure a record exists.

    Agents are request-driven — there is nothing to "start".  This
    endpoint only preloads the workspace so subsequent requests are
    fast; the agent graph itself is built lazily on first use.
    """
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
    """Unload an agent: release in-memory state, keep everything on disk.

    Idempotent.  The workspace is unloaded from memory (flushed to disk
    — files stay in place) and the cached agent graph is dropped so a
    later chat rebuilds the agent from the persisted configuration.
    Session checkpoints are **not** touched — unloading never loses
    conversation state.  On-disk files are only removed by DELETE.
    """
    service = _get_service(request)
    try:
        await service.stop_agent(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found") from exc
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

    # Drop the cached agent graph so the next chat rebuilds it.  Do NOT
    # purge session checkpoints — unloading must never lose conversation
    # state (checkpoints carry files / todos / compressed memory).
    try:
        await invalidate_agent_graph(
            request.app.state, agent_id, purge_checkpoints=False
        )
    except Exception:
        logger.exception(
            "Failed to invalidate agent graph on stop for %s", agent_id
        )

    # --- Sandbox lifecycle: stop agent triggers sandbox cleanup ---
    # Strategy A (ephemeral) and B (pause-on-idle) destroy the sandbox.
    # Strategy C (persistent) keeps it running.
    sandbox_mgr = getattr(request.app.state, "sandbox_session_manager", None)
    if sandbox_mgr is not None:
        try:
            await sandbox_mgr.on_agent_stop(agent_id)
        except Exception:
            logger.exception(
                "Failed to handle sandbox on agent stop for %s", agent_id
            )

    return {"result": "ok", "agent_id": agent_id, "state": "idle"}


@router.post("/{agent_id}/reload")
async def reload_agent(agent_id: str, request: Request) -> dict[str, str]:
    """Hot-reload the agent's workspace without affecting other agents.

    Works whether or not the workspace is currently loaded — an unloaded
    (e.g. just-stopped) agent is loaded first, then reloaded.
    """
    manager = _get_manager(request)
    workspace = manager.get_workspace(agent_id)
    if workspace is None:
        if agent_id not in _known_agent_ids(request):
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
        workspace = await manager.get_or_create_workspace(agent_id)

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

    # Dynamic discovery (Task 2.3): also invalidate other agents that
    # aggregate subagents so they pick up the changed config.
    await _invalidate_subagent_consumers(request)

    return {"status": "reloaded", "agent_id": agent_id}


@router.get("/{agent_id}/kernel/{filename}")
async def get_kernel_file(
    agent_id: str, filename: str, request: Request
) -> dict[str, Any]:
    """Read a kernel file (``bootstrap.md`` / ``agent.md``, plus legacy files)."""
    if filename not in ALL_KERNEL_FILE_NAMES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown kernel file {filename!r}; expected one of "
            f"{list(ALL_KERNEL_FILE_NAMES)}",
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
    if filename not in ALL_KERNEL_FILE_NAMES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown kernel file {filename!r}; expected one of "
            f"{list(ALL_KERNEL_FILE_NAMES)}",
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
    if payload.api_key is not None:
        model_section["api_key"] = payload.api_key
    if payload.headers is not None:
        model_section["headers"] = payload.headers
    if payload.extra_body is not None:
        model_section["extra_body"] = payload.extra_body
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


@router.put("/{agent_id}/settings")
async def put_agent_settings(
    agent_id: str,
    payload: UpdateSettingsRequest,
    request: Request,
) -> dict[str, Any]:
    """Update the agent's ``settings`` section of ``agent.json``.

    Only fields present in *payload* are updated; ``description`` /
    ``system_prompt`` accept empty strings to clear them.  All other
    sections of ``agent.json`` (model / tools / …) are preserved.  The
    cached agent graph is invalidated so the next chat uses the new
    configuration.
    """
    manager = _get_manager(request)
    workspace = manager.get_workspace(agent_id)
    if workspace is None:
        if agent_id not in _known_agent_ids(request):
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
        workspace = await manager.get_or_create_workspace(agent_id)

    config = workspace.read_agent_config()
    settings: dict[str, Any] = dict(config.get("settings") or {})
    if payload.description is not None:
        settings["description"] = payload.description
    if payload.system_prompt is not None:
        settings["system_prompt"] = payload.system_prompt
    if payload.enable_subagents is not None:
        settings["enable_subagents"] = payload.enable_subagents
    if payload.inherit_parent_tools is not None:
        settings["inherit_parent_tools"] = payload.inherit_parent_tools
    if payload.enable_planning is not None:
        settings["enable_planning"] = payload.enable_planning
    if payload.enable_a2ui is not None:
        settings["enable_a2ui"] = payload.enable_a2ui
    if payload.interrupt_rules is not None:
        # Empty list clears HITL rules; otherwise replace wholesale.
        settings["interrupt_rules"] = payload.interrupt_rules
    if payload.subagent_ids is not None:
        # Empty list means "all agents"; non-empty restricts delegation.
        settings["subagent_ids"] = payload.subagent_ids
    config["settings"] = settings

    # Handle model_id: resolve from model registry and update model section
    if payload.model_id is not None:
        from agentcore.runtime.models_router import model_store

        registry = model_store.load_model_registry()
        model_entry = next((m for m in registry if m.get("id") == payload.model_id), None)
        if model_entry:
            config["model"] = {
                "provider": model_entry.get("provider") or "",
                "name": model_entry.get("name") or "",
                "base_url": model_entry.get("base_url") or "",
                "api_key_env": model_entry.get("api_key_env") or "",
            }
        elif payload.model_id == "":
            # Clear model configuration
            config.pop("model", None)

    try:
        workspace.write_agent_config(config)
    except Exception as exc:
        logger.exception("Failed to update settings for agent %s", agent_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update settings for {agent_id!r}: {exc}",
        ) from exc

    # Force the agent to be rebuilt with the new settings on the next chat.
    await invalidate_agent_graph(request.app.state, agent_id)

    # Topology may have changed (enable_subagents / description) — refresh
    # consumers that aggregate this agent as a subagent.
    await _invalidate_subagent_consumers(request)

    return {"result": "ok", "agent_id": agent_id, "settings": settings}


@router.get("/{agent_id}/delegations")
async def list_delegations(agent_id: str, request: Request) -> dict[str, Any]:
    """Return delegation records received by *agent_id* (newest last).

    Records are appended whenever another agent invokes this agent via
    the ``task`` tool, so a subagent's own chat page can show who
    delegated what to it.
    """
    store = _get_store(request)
    return {"agent_id": agent_id, "delegations": store.list_delegations(agent_id)}


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, request: Request) -> dict[str, str]:
    """Delete an agent: shut down its workspace and drop persisted state.

    Also removes every scheduled entry point that could resurrect the
    agent (heartbeat internal job + user cron jobs targeting it) —
    otherwise the next scheduled run would recreate the workspace via
    lazy loading and bring the "deleted" agent back to life.

    Full cleanup covers:

    * cron / heartbeat jobs (resurrection guard),
    * sandbox session,
    * workspace directory (``.agentcore/workspace/agent/{id}/``),
    * runtime data directory (``.agentcore/data/agents/{id}/`` — sessions
      and other runtime artefacts),
    * persisted agent state (``agents.json``),
    * ControlPlaneStore sessions belonging to this agent,
    * delegation records referencing this agent,
    * runtime tracking instance.
    """
    if agent_id not in _known_agent_ids(request):
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    # Scheduled resurrection guard: drop heartbeat + memory-consolidation
    # internal jobs and every user cron job that targets this agent.
    # Missing any one of them leaves a scheduled callback that will call
    # ``get_or_create_workspace`` on the next fire and recreate the
    # on-disk workspace directory (zombie agent after restart).
    cron_manager = getattr(request.app.state, "cron_manager", None)
    if cron_manager is not None:
        try:
            cron_manager.remove_internal_job(heartbeat_job_id(agent_id))
            cron_manager.remove_internal_job(consolidation_job_id(agent_id))
            removed = await cron_manager.delete_jobs_for_agent(agent_id)
            if removed:
                logger.info(
                    "Removed %d cron job(s) of deleted agent %s",
                    removed,
                    agent_id,
                )
        except Exception:
            logger.exception(
                "Failed to remove scheduled jobs for deleted agent %s", agent_id
            )

    # --- Sandbox lifecycle: delete agent always destroys sandbox ---
    # All strategies (ephemeral/pause-on-idle/persistent) destroy the
    # sandbox when the agent is deleted.
    sandbox_mgr = getattr(request.app.state, "sandbox_session_manager", None)
    if sandbox_mgr is not None:
        try:
            await sandbox_mgr.destroy(agent_id)
        except Exception:
            logger.exception(
                "Failed to destroy sandbox for deleted agent %s", agent_id
            )

    # --- Workspace directory (kernel files / skills / agent.json) ---
    manager = _get_manager(request)
    await manager.remove_workspace(agent_id)

    # --- Runtime data directory (sessions / other artefacts) ---
    # Sessions are persisted at .agentcore/data/agents/{agent_id}/sessions/;
    # the whole data directory is removed here so no orphan session files
    # survive the deletion.
    data_dir = paths.get_agent_data_dir(agent_id)
    if data_dir.exists():
        _rmtree_with_retry(data_dir)
        if not data_dir.exists():
            logger.info("Removed agent data directory for %s", agent_id)
        else:
            logger.warning(
                "Agent data directory for %s still exists after removal attempt",
                agent_id,
            )

    # --- ControlPlaneStore sessions belonging to this agent ---
    store = getattr(request.app.state, "store", None)
    if store is not None:
        try:
            orphan_sids = [
                sid
                for sid, data in store.sessions.items()
                if data.get("agent_id") == agent_id
            ]
            if orphan_sids:
                store.delete_sessions(orphan_sids)
                logger.info(
                    "Removed %d session(s) of deleted agent %s",
                    len(orphan_sids),
                    agent_id,
                )
        except Exception:
            logger.exception(
                "Failed to remove sessions for deleted agent %s", agent_id
            )

        # --- Delegation records ---
        try:
            delegations = getattr(store, "delegations", None)
            if delegations is not None and agent_id in delegations:
                delegations.pop(agent_id, None)
                store._save_delegations()
                logger.info("Removed delegation records for agent %s", agent_id)
        except Exception:
            logger.exception(
                "Failed to remove delegation records for agent %s", agent_id
            )

    # --- Runtime tracking instance (and persisted agent state) ---
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is not None:
        await runtime.remove_agent(agent_id)

    # --- Drop the agent's own cached graph + thread index ---
    # Without this a still-open browser tab could hit ``/chat/stream``
    # and the graph-cache fast-path would keep serving the deleted
    # agent, which in turn would call ``begin_turn`` and re-persist its
    # state (see the fix in AgentRuntime.begin_turn).
    try:
        await invalidate_agent_graph(request.app.state, agent_id)
    except Exception:
        logger.exception(
            "Failed to invalidate graph cache for deleted agent %s", agent_id
        )

    # Dynamic discovery (Task 2.3): invalidate subagent consumers so they
    # drop the removed agent from their delegation list.
    await _invalidate_subagent_consumers(request)

    return {"status": "deleted", "agent_id": agent_id}


@router.get("/{agent_id}/sessions")
async def list_sessions(agent_id: str, request: Request) -> dict[str, Any]:
    """List all chat sessions belonging to the agent's workspace.

    Sessions live on disk independently of the workspace's load state, so
    an unloaded (e.g. just-stopped) agent is loaded transparently.
    """
    if agent_id not in _known_agent_ids(request):
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    manager = _get_manager(request)
    workspace = manager.get_workspace(agent_id)
    if not workspace:
        workspace = await manager.get_or_create_workspace(agent_id)
    return {"sessions": workspace.chat_manager.list_sessions()}


@router.get("/{agent_id}/sessions/{session_id}/history")
async def get_session_history(
    agent_id: str,
    session_id: str,
    request: Request,
    limit: int = 50,
) -> dict[str, Any]:
    """Return the most recent *limit* messages of a session."""
    if agent_id not in _known_agent_ids(request):
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    manager = _get_manager(request)
    workspace = manager.get_workspace(agent_id)
    if not workspace:
        workspace = await manager.get_or_create_workspace(agent_id)
    try:
        history = workspace.chat_manager.get_history(session_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_key_error_detail(exc)) from exc
    return {"messages": history}
