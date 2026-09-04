"""Chat API router: synchronous chat endpoints with session management.

Provides a simple synchronous chat endpoint that invokes agents via the
deepagents SDK.  Agent configuration is resolved from the workspace's
``agent.json`` file and agent graphs are built through the shared
:class:`~agentcore.runtime.agent_factory.AgentFactory` (the single
config→``create_deep_agent`` mapping used by every runtime path).
Created agent graphs are cached in-memory for reuse.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Literal

import aiosqlite
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field

# PostgreSQL checkpointer (lazy import to avoid hard dependency)
try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
except ImportError:
    AsyncPostgresSaver = None  # type: ignore[misc,assignment]

from agentcore.runtime import paths
from agentcore.runtime.agent_ids import known_agent_ids
from agentcore.runtime.agent_runtime import mark_turn_begin, mark_turn_end
from agentcore.runtime.approval_cleanup import has_expired_approval
from agentcore.runtime.sse_heartbeat import keepalive_sse
from agentcore.runtime.workspace import (
    ALL_KERNEL_FILE_NAMES,
    BOOTSTRAP_MD_NAME,
    MEMORY_DIR_NAME,
    SKILLS_DIR_NAME,
)

# Backwards-compatible key used by LangGraph v1 ainvoke() to surface
# interrupts in the result dict.  Value: list[Interrupt].
_INTERRUPT_KEY = "__interrupt__"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-application chat state (checkpointer + agent graph cache)
#
# All mutable chat runtime state lives in a :class:`ChatState` instance
# mounted on ``app.state.chat_state`` by the application lifespan — NOT in
# module-level globals.  This keeps one FastAPI app instance self-contained
# (required for lifespan-based hosting) and makes the state easy to reset.
#
# :data:`_default_chat_state` is a fallback used only when the app runs
# without lifespan (e.g. synchronous test clients or direct unit calls).
# ---------------------------------------------------------------------------


@dataclass
class ChatState:
    """Mutable chat runtime state owned by a single FastAPI application."""

    checkpointer: AsyncSqliteSaver | "AsyncPostgresSaver" | None = None
    # Compiled agent graphs keyed by agent_id — rebuilt on invalidation.
    graph_cache: dict[str, Any] = field(default_factory=dict)
    # LangGraph thread_ids (= session_ids) used per agent so graph
    # invalidation can also purge checkpointed state.  The SDK's
    # memory/skills middleware load only once per thread (contents are
    # cached in checkpoint state), so stale threads would otherwise keep
    # outdated kernel files / skill lists forever after a reload.
    threads: dict[str, set[str]] = field(default_factory=dict)


_default_chat_state = ChatState()


def get_chat_state(state: Any) -> ChatState:
    """Resolve the :class:`ChatState` from ``app.state``.

    Accepts a Starlette state object carrying a ``chat_state`` attribute
    (populated during lifespan) and falls back to the process-wide default
    when absent — keeping the app usable without lifespan.
    """
    chat_state = getattr(state, "chat_state", None)
    if chat_state is None:
        chat_state = _default_chat_state
    return chat_state


async def get_checkpointer(chat_state: ChatState) -> AsyncSqliteSaver | "AsyncPostgresSaver":
    """Return the application-scoped LangGraph checkpointer.

    根据 ``AGENTCORE_CHECKPOINTER_BACKEND`` 环境变量选择后端：

    - ``sqlite`` (默认): 使用 :class:`AsyncSqliteSaver`
    - ``postgresql``: 使用 :class:`AsyncPostgresSaver`

    两种实现都遵循 LangGraph 标准 checkpointer 接口，
    deep agents SDK 无需任何修改。
    """
    if chat_state.checkpointer is not None:
        return chat_state.checkpointer

    if paths.is_postgres_enabled():
        # === PostgreSQL 模式 ===
        if AsyncPostgresSaver is None:
            raise ImportError(
                "PostgreSQL checkpointer requires 'langgraph-checkpoint-postgres' package. "
                "Install with: pip install langgraph-checkpoint-postgres"
            )
        import asyncpg

        conn_string = paths.get_postgres_connection_string()
        conn = await asyncpg.connect(conn_string)
        chat_state.checkpointer = AsyncPostgresSaver(conn)

        # 首次启动时创建表（幂等操作）
        await chat_state.checkpointer.setup()

        logger.info(
            "LangGraph checkpointer initialised (PostgreSQL): %s:%s/%s",
            paths.get_postgres_host(),
            paths.get_postgres_port(),
            paths.get_postgres_db(),
        )
    else:
        # === SQLite 模式（默认）===
        checkpoint_db = paths.get_checkpoints_path()
        checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
        conn = aiosqlite.connect(str(checkpoint_db))
        chat_state.checkpointer = AsyncSqliteSaver(conn)
        logger.info("LangGraph checkpointer initialised (SQLite): %s", checkpoint_db)

    return chat_state.checkpointer


async def close_checkpointer(chat_state: ChatState) -> None:
    """Close the checkpointer connection (called on app shutdown).

    Without this the background connection thread keeps the
    interpreter alive at exit.
    """
    if chat_state.checkpointer is not None:
        try:
            await chat_state.checkpointer.conn.close()
        except Exception:
            logger.exception("Failed to close checkpointer connection")
        finally:
            chat_state.checkpointer = None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


def _get_store(request: Request):
    """Extract :class:`ControlPlaneStore` from ``request.app.state``."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="ControlPlaneStore is not available.",
        )
    return store


# ---------------------------------------------------------------------------
# Agent graph cache (lives in app.state — see ChatState above)
# ---------------------------------------------------------------------------


async def invalidate_agent_graph(
    state: Any, agent_id: str | None = None, *, purge_checkpoints: bool = True
) -> None:
    """Drop cached agent graphs so they are rebuilt with fresh config.

    *state* is the owning application's ``app.state`` (the chat state is
    resolved through :func:`get_chat_state`).  Pass an *agent_id* to
    invalidate a single agent, or ``None`` to clear the entire cache.
    Called after workspace reload / kernel-file edits so the next chat
    recreates the agent with the new configuration.

    With ``purge_checkpoints=True`` (the default for config-change
    paths) the checkpoint state of every tracked session belonging to
    the invalidated agent(s) is deleted as well — the SDK's
    ``MemoryMiddleware`` / ``SkillsMiddleware`` skip loading when
    ``memory_contents`` / ``skills_metadata`` already exist in
    checkpointed state, so without this purge kernel-file or skill
    changes would never reach existing conversations.

    ``purge_checkpoints=False`` is used by the unload/stop path, which
    releases memory without altering configuration — deleting session
    checkpoints there would silently lose conversation state.
    """
    chat_state = get_chat_state(state)
    if agent_id is None:
        targets = sorted(set(chat_state.graph_cache) | set(chat_state.threads))
        chat_state.graph_cache.clear()
        logger.info("Agent graph cache fully invalidated")
    else:
        targets = [agent_id]
        if chat_state.graph_cache.pop(agent_id, None) is not None:
            logger.info("Agent graph cache invalidated for %s", agent_id)

    # Purge checkpoint state of the affected sessions so memory/skills are
    # re-read from disk on the next invoke (config-change paths only).
    thread_ids: set[str] = set()
    for aid in targets:
        thread_ids |= chat_state.threads.pop(aid, set())
    if purge_checkpoints and thread_ids and chat_state.checkpointer is not None:
        for thread_id in thread_ids:
            try:
                await chat_state.checkpointer.adelete_thread(thread_id)
            except Exception:
                logger.exception(
                    "Failed to delete checkpoint state for thread %s", thread_id
                )
        logger.info("Checkpoint state purged for %d session(s)", len(thread_ids))


# ---------------------------------------------------------------------------
# Sandbox workspace sync — upload local workspace files to sandbox container
# ---------------------------------------------------------------------------


async def _sandbox_write_content(
    backend: Any,
    path: str,
    content: str | bytes,
) -> None:
    """Write content to a sandbox path, deleting any existing file first.

    The ``OpenSandboxBackend.upload_files()`` (inherited from
    ``BaseSandbox``) rejects files that already exist, so we must
    ``rm -f`` before each write to support re-syncing to the same
    sandbox container.
    """
    import shlex
    await backend.aexecute(f"rm -f {shlex.quote(path)}")
    if isinstance(content, bytes):
        await backend.aupload_files([(path, content)])
    else:
        await backend.awrite(path, content)


async def _legacy_sync_workspace_to_sandbox(
    workspace: Any,
    sandbox_backend: Any,
) -> None:
    """Upload workspace skills, memory, and kernel files to the sandbox.

    When the agent uses a sandbox backend, the SDK's SkillsMiddleware and
    MemoryMiddleware read files through the backend (i.e. inside the
    sandbox container).  But the files live on the local workspace
    filesystem — the sandbox container starts with an empty filesystem.
    This function bridges the gap by uploading the relevant files before
    agent creation so the SDK can discover them.
    """
    workspace_dir = workspace.workspace_dir
    uploaded = 0

    # --- Skills ---
    skills_dir = workspace_dir / SKILLS_DIR_NAME
    if skills_dir.exists() and skills_dir.is_dir():
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            # Upload SKILL.md
            try:
                content = skill_md.read_text(encoding="utf-8")
                sandbox_path = f"/{SKILLS_DIR_NAME}/{skill_dir.name}/SKILL.md"
                await _sandbox_write_content(sandbox_backend, sandbox_path, content)
                uploaded += 1
            except Exception:
                logger.exception(
                    "Failed to upload skill %s to sandbox", skill_dir.name,
                )
            # Upload all other files in skill directory (recursively)
            for item in skill_dir.rglob("*"):
                if not item.is_file():
                    continue
                if item.name == "SKILL.md":
                    continue  # Already uploaded
                if item.name == ".DS_Store":
                    continue  # Skip macOS metadata
                try:
                    rel = item.relative_to(skill_dir)
                    content = item.read_bytes()
                    sandbox_path = f"/{SKILLS_DIR_NAME}/{skill_dir.name}/{rel.as_posix()}"
                    await _sandbox_write_content(sandbox_backend, sandbox_path, content)
                    uploaded += 1
                except Exception:
                    logger.exception(
                        "Failed to upload skill file %s to sandbox", item.name,
                    )

    # --- Memory files ---
    memory_dir = workspace_dir / MEMORY_DIR_NAME
    if memory_dir.exists() and memory_dir.is_dir():
        for mem_file in ["MEMORY.md", "USER.md"]:
            mem_path = memory_dir / mem_file
            if mem_path.exists():
                try:
                    content = mem_path.read_text(encoding="utf-8")
                    sandbox_path = f"/{MEMORY_DIR_NAME}/{mem_file}"
                    await _sandbox_write_content(sandbox_backend, sandbox_path, content)
                    uploaded += 1
                except Exception:
                    logger.exception(
                        "Failed to upload memory %s to sandbox", mem_file,
                    )

    # --- Kernel files (bootstrap.md, agent.md, etc.) ---
    for name in ALL_KERNEL_FILE_NAMES:
        kernel_path = workspace_dir / name
        if kernel_path.exists():
            try:
                content = kernel_path.read_text(encoding="utf-8")
                await _sandbox_write_content(sandbox_backend, f"/{name}", content)
                uploaded += 1
            except Exception:
                logger.exception(
                    "Failed to upload kernel file %s to sandbox", name,
                )

    # --- Data files (Excel, CSV, etc. in workspace root) ---
    _DATA_EXTENSIONS = {".xlsx", ".xls", ".csv", ".json", ".txt", ".md"}
    for item in workspace_dir.iterdir():
        if not item.is_file():
            continue
        if item.suffix.lower() not in _DATA_EXTENSIONS:
            continue
        # Skip kernel files (already synced above)
        if item.name in ALL_KERNEL_FILE_NAMES:
            continue
        try:
            content = item.read_bytes()
            await _sandbox_write_content(sandbox_backend, f"/{item.name}", content)
            uploaded += 1
        except Exception:
            logger.exception(
                "Failed to upload data file %s to sandbox", item.name,
            )

    if uploaded:
        logger.info(
            "Synced %d workspace file(s) to sandbox %s",
            uploaded,
            getattr(sandbox_backend, "id", "?"),
        )


async def _sync_workspace_to_sandbox(workspace: Any, sandbox_backend: Any) -> None:
    """Mirror the workspace into the container before the turn starts.

    When file sync is enabled (default for sandbox agents) the whole
    workspace is pushed via :class:`SandboxSyncEngine` — skills, memory,
    kernel files and user data alike — after waiting for the freshly
    created container's AIO server to answer (it 502s while booting).
    Agents that opted out (``file_sync.enabled = false``) fall back to
    the legacy per-file upload of skills/memory/kernel to the container
    root.
    """
    from agentcore.sandbox.sync_engine import SandboxSyncEngine, wait_backend_ready

    try:
        agent_config = workspace.read_agent_config()
    except Exception:
        agent_config = {}
    engine = SandboxSyncEngine.from_agent_config(
        workspace.workspace_dir, agent_config
    )
    if engine is None:
        await _legacy_sync_workspace_to_sandbox(workspace, sandbox_backend)
        return
    # Bind the current container id to the sync engine.  When the container
    # has been recreated (system restart, sandbox death, manual destroy) the
    # manifest is reset so push() re-uploads every file instead of skipping
    # them all as "unchanged" against a stale manifest.
    container_id = getattr(sandbox_backend, "id", None)
    if container_id:
        engine.note_container_id(str(container_id))
    # A freshly created container needs a moment before its AIO server
    # answers; waiting here avoids 502s on the very first chat (which
    # previously dropped every skill/memory upload silently).
    if not await wait_backend_ready(sandbox_backend, timeout=60):
        logger.warning("Sandbox not ready after 60s — skipping workspace push")
        return
    report = await engine.push(sandbox_backend)
    if report["pushed"]:
        logger.info(
            "Pushed %d workspace file(s) to sandbox %s",
            len(report["pushed"]),
            getattr(sandbox_backend, "id", "?"),
        )


# Detached pull tasks — kept referenced so the event loop does not GC them.
_PULL_TASKS: set[asyncio.Task] = set()


async def _pull_sandbox_storage(
    app_state: Any, agent_id: str, workspace: Any = None
) -> None:
    """Best-effort pull of the sandbox home back into the workspace.

    Runs after a chat turn finishes so files the agent created inside the
    container land in the workspace without a manual sync.  Failures are
    logged and never surfaced — a dead container must not break the UI.
    """
    from agentcore.sandbox.sync_engine import SandboxSyncEngine

    try:
        if app_state is None:
            return
        mgr = getattr(app_state, "sandbox_session_manager", None)
        if mgr is None:
            return
        session = mgr.get_session(agent_id)
        if session is None or session.status != "running":
            return
        if workspace is None:
            agent_manager = getattr(app_state, "agent_manager", None)
            if agent_manager is None:
                return
            workspace = agent_manager.get_workspace(agent_id)
            if workspace is None:
                return
        try:
            agent_config = workspace.read_agent_config()
        except Exception:
            agent_config = {}
        engine = SandboxSyncEngine.from_agent_config(
            workspace.workspace_dir, agent_config
        )
        if engine is None:
            return
        container_id = getattr(session.backend, "id", None) or session.sandbox_id
        engine.note_container_id(str(container_id))
        report = await engine.pull(session.backend)
        if report["pulled"] or report["conflicts"]:
            logger.info(
                "Pulled %d file(s) from sandbox %s (%d conflict(s))",
                len(report["pulled"]),
                session.sandbox_id,
                len(report["conflicts"]),
            )
    except Exception:
        logger.exception(
            "Failed to pull sandbox storage for agent %s", agent_id
        )


def _schedule_pull(app_state: Any, agent_id: str, workspace: Any) -> None:
    """Fire the post-turn sandbox pull as a detached background task.

    Detaching keeps the SSE ``done`` event snappy and ensures a client
    disconnect (generator close) never cancels the transfer.
    """
    try:
        task = asyncio.get_running_loop().create_task(
            _pull_sandbox_storage(app_state, agent_id, workspace)
        )
        _PULL_TASKS.add(task)
        task.add_done_callback(_PULL_TASKS.discard)
    except RuntimeError:
        pass  # No running loop (shouldn't happen inside the generators).


# ---------------------------------------------------------------------------
# Agent graph resolution
# ---------------------------------------------------------------------------


async def _ensure_agent_graph(
    req: Request,
    agent_id: str,
    chat_state: ChatState,
) -> Any:
    """Return a cached graph for *agent_id*, rebuilding it if necessary.

    After a server restart the in-memory ``graph_cache`` is empty.  The
    main ``stream_chat`` endpoint already handles this because it always
    goes through :func:`_resolve_agent_graph` (which rebuilds on cache
    miss).  The approval endpoints, however, used to read directly from
    ``graph_cache`` and raise a 404 on miss — leaving the user stuck
    after a restart.  This helper gives them the same auto-rebuild
    behaviour.
    """
    # Fast path: cache hit.
    if agent_id in chat_state.graph_cache:
        return chat_state.graph_cache[agent_id]

    # Zombie-resurrection guard: never lazy-load a workspace to rebuild
    # a graph for an agent that no longer has a persisted record.  Without
    # this, a stale browser tab calling an approval endpoint would
    # silently recreate ``.agentcore/workspace/agent/{id}/`` from scratch
    # and undo the earlier delete.
    if agent_id not in known_agent_ids(req.app.state):
        raise HTTPException(
            status_code=404,
            detail=f"Agent {agent_id!r} not found",
        )

    # Slow path: rebuild.
    manager = getattr(req.app.state, "agent_manager", None)
    workspace = None
    if manager is not None:
        try:
            workspace = await manager.get_or_create_workspace(agent_id)
        except Exception:
            logger.exception("Failed to load workspace for agent %s", agent_id)

    # Sandbox backend (if applicable)
    sandbox_backend = None
    sandbox_mgr = getattr(req.app.state, "sandbox_session_manager", None)
    agent_config: dict = {}
    if workspace is not None:
        try:
            agent_config = workspace.read_agent_config()
        except Exception:
            logger.exception("Failed to read agent config for %s", agent_id)
    if sandbox_mgr is not None and workspace is not None:
        try:
            backend_cfg = (agent_config.get("settings") or {}).get("backend", {})
            if backend_cfg.get("type") == "sandbox":
                session = await sandbox_mgr.get_or_create(agent_id, backend_cfg)
                if session is not None:
                    sandbox_backend = session.backend
                    # Sync workspace files to sandbox container
                    try:
                        await _sync_workspace_to_sandbox(workspace, sandbox_backend)
                    except Exception:
                        logger.exception(
                            "Failed to sync workspace to sandbox for agent %s",
                            agent_id,
                        )
        except Exception:
            logger.exception(
                "Failed to get/create sandbox session for agent %s", agent_id,
            )

    factory = getattr(req.app.state, "factory", None)
    return await _resolve_agent_graph(
        agent_id,
        workspace,
        factory=factory,
        manager=manager,
        chat_state=chat_state,
        sandbox_backend=sandbox_backend,
    )


async def _resolve_agent_graph(
    agent_id: str,
    workspace: Any,
    factory: Any = None,
    manager: Any = None,
    chat_state: ChatState | None = None,
    sandbox_backend: Any = None,
) -> Any:
    """Resolve *agent_id* to a compiled agent graph.

    Lookup strategy:

    1. Check the in-memory cache.
    2. Read the agent configuration from the workspace's ``agent.json``.
    3. When ``settings["enable_subagents"]`` is enabled, aggregate the
       other loaded agents (via
       :class:`~agentcore.runtime.subagent_registry.SubAgentRegistry`)
       as deepagents ``SubAgent`` specs so this agent can delegate to
       them through the SDK's ``task`` tool.
    4. Delegate graph construction to the shared
       :class:`~agentcore.runtime.agent_factory.AgentFactory`, which
       applies the full configuration mapping — model, workspace-bound
       backend, permissions, HITL ``interrupt_on`` and tool exclusion —
       plus the workspace kernel files (``memory=[]``) and ``skills/``
       directory (``skills=[]``).
    5. Cache the resulting graph.
    """
    if chat_state is None:
        chat_state = _default_chat_state
    if agent_id in chat_state.graph_cache:
        # If a sandbox_backend is provided, verify the cached graph was built
        # with the same backend.  The backend adapter is bound to a specific
        # sandbox container, so a stale graph would execute commands in a dead
        # container.  Compare by sandbox_id when available.
        if sandbox_backend is not None:
            cached_graph = chat_state.graph_cache[agent_id]
            cached_backend = getattr(cached_graph, "_sandbox_backend", None)
            new_sid = getattr(sandbox_backend, "sandbox_id", None) or getattr(sandbox_backend, "id", None)
            old_sid = (getattr(cached_backend, "sandbox_id", None) or getattr(cached_backend, "id", None)) if cached_backend else None
            if new_sid and old_sid and new_sid != old_sid:
                logger.info(
                    "Sandbox backend changed for agent %s (%s → %s) — rebuilding graph",
                    agent_id, old_sid, new_sid,
                )
                chat_state.graph_cache.pop(agent_id, None)
            elif new_sid and not old_sid:
                # Cached graph has no backend reference (built before fix)
                # or is for a non-sandbox agent — force rebuild
                logger.info(
                    "Cached graph for agent %s has no sandbox backend ref (new=%s) — rebuilding",
                    agent_id, new_sid,
                )
                chat_state.graph_cache.pop(agent_id, None)
            else:
                return cached_graph
        else:
            return chat_state.graph_cache[agent_id]

    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Workspace for agent {agent_id!r} is not available. "
                "Create the agent first (POST /api/agents)."
            ),
        )

    # Build the agent via the shared AgentFactory — the single
    # agent.json→create_deep_agent mapping.  The factory binds the
    # backend to the workspace directory, injects kernel files via
    # ``memory=[]``, exposes ``skills/``, and applies permissions /
    # HITL / tool exclusion from the configuration's settings.
    if factory is None:
        from agentcore.runtime.agent_factory import AgentFactory

        factory = AgentFactory()

    agent_config = workspace.read_agent_config()
    workspace_dir = workspace.workspace_dir

    # --- Opt-in subagents: inject the other loaded agents --------------
    # Disabled by default so agents stay mutually invisible unless an
    # agent explicitly sets ``settings.enable_subagents: true``.
    subagents: list[Any] | None = None
    settings = agent_config.get("settings", {}) or {}

    # Global security settings (danger-operation approval) apply to every
    # agent: merge them into the config before building the graph so the
    # factory maps the combined ``interrupt_rules`` onto ``interrupt_on``.
    from agentcore.runtime.security_router import apply_global_approval

    merged_settings = apply_global_approval(settings)
    if merged_settings is not settings:
        agent_config = {**agent_config, "settings": merged_settings}
        settings = merged_settings

    if settings.get("enable_subagents") and manager is not None:
        from agentcore.runtime.subagent_registry import SubAgentRegistry

        # Pass subagent_ids whitelist if configured (None = all agents)
        subagent_whitelist = settings.get("subagent_ids") or None
        subagents = SubAgentRegistry(manager).get_subagents(
            exclude_agent_id=agent_id,
            whitelist=subagent_whitelist,
        )
        logger.info(
            "Agent %s: injecting %d subagent(s): %s",
            agent_id,
            len(subagents),
            [spec["name"] for spec in subagents],
        )

    # LangGraph checkpointer — enables multi-turn conversation memory:
    # each thread_id (= session_id) keeps its full message history so the
    # model sees prior turns during inference.
    logger.info("Creating agent %s via AgentFactory", agent_id)
    try:
        agent_graph = factory.create_agent(
            agent_config,
            workspace_dir=workspace_dir,
            workspace=workspace,
            checkpointer=await get_checkpointer(chat_state),
            subagents=subagents,
            sandbox_backend=sandbox_backend,
        )
    except Exception as exc:
        logger.exception("Failed to create agent %s", agent_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create agent {agent_id!r}: {exc}",
        ) from exc

    # Store the sandbox_backend on the graph so cache validation can
    # detect when the sandbox container has changed.
    if sandbox_backend is not None:
        agent_graph._sandbox_backend = sandbox_backend

    chat_state.graph_cache[agent_id] = agent_graph
    return agent_graph


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class Attachment(BaseModel):
    """Reference to a file uploaded via ``POST /api/chat/upload``."""

    name: str  # original (sanitised) file name
    path: str  # workspace-relative path, e.g. ``uploads/ab12cd_report.pdf``
    size: int = 0


class ChatRequest(BaseModel):
    """Incoming chat message."""

    agent_id: str
    message: str
    session_id: str | None = None  # auto-created when omitted
    attachments: list[Attachment] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """Complete assistant reply."""

    session_id: str
    agent_id: str
    content: str
    timestamp: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "complete"
    approval_request: dict[str, Any] | None = None


class ApprovalRequest(BaseModel):
    """Incoming approval decision for a HITL interrupt."""

    decision: Literal["approve", "edit", "reject", "respond"]
    edited_args: dict[str, Any] | None = None
    operator: str | None = None
    message: str | None = None  # optional rejection message


class SessionInfo(BaseModel):
    """Lightweight session descriptor returned by list endpoint."""

    session_id: str
    agent_id: str
    created_at: str
    updated_at: str
    message_count: int
    has_pending_approval: bool = False
    has_expired_approval: bool = False
    title: str = ""  # User-assigned session title (empty = auto-generated)


class BatchDeleteSessionsRequest(BaseModel):
    """Incoming batch session-delete request."""

    session_ids: list[str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_interrupts_from_result(result: Any) -> list[Any]:
    """Extract ``Interrupt`` objects from an agent result dict.

    LangGraph v1 ``ainvoke()`` includes ``__interrupt__`` in the result
    dict when the graph paused due to an interrupt (e.g. HITL approval).
    """
    if not isinstance(result, dict):
        return []
    interrupts = result.get(_INTERRUPT_KEY)
    if interrupts is None:
        return []
    return list(interrupts) if isinstance(interrupts, (list, tuple)) else []


def _build_approval_request_info(interrupts: list[Any]) -> dict[str, Any]:
    """Build a serialisable approval-request payload from interrupts.

    The SDK's ``HumanInTheLoopMiddleware`` passes a ``HITLRequest`` dict
    as the interrupt value, containing ``action_requests`` and
    ``review_configs``.  Each action carries its ``allowed_decisions``
    (``None`` when the policy is absent — the client then renders the
    full default button set).
    """
    actions: list[dict[str, Any]] = []
    for interrupt_obj in interrupts:
        value = getattr(interrupt_obj, "value", None)
        if value is None and isinstance(interrupt_obj, dict):
            value = interrupt_obj.get("value")
        if not isinstance(value, dict):
            continue
        action_requests = value.get("action_requests", [])
        review_configs = value.get("review_configs", [])
        rc_by_name = {
            rc.get("action_name"): rc
            for rc in review_configs
            if isinstance(rc, dict) and rc.get("action_name")
        }
        for ar in action_requests:
            rc = rc_by_name.get(ar.get("name"), {})
            actions.append({
                "name": ar.get("name", ""),
                "args": ar.get("args", {}),
                "description": ar.get("description", ""),
                "allowed_decisions": rc.get("allowed_decisions") or None,
            })
    return {"actions": actions}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _attachment_extras(attachments: list[Attachment]) -> dict[str, Any] | None:
    """Build the persistence extras dict for a user message's attachments."""
    if not attachments:
        return None
    return {"attachments": [a.model_dump() for a in attachments]}


def _with_attachment_note(message: str, attachments: list[Attachment]) -> str:
    """Append a note to the model input describing uploaded attachments.

    Uploaded files live inside the agent's workspace (``uploads/``), so the
    agent's built-in file tools can read them — the note tells the model
    where they are and that it should inspect them before answering.
    """
    if not attachments:
        return message
    listing = "; ".join(f"{a.name} -> {a.path}" for a in attachments)
    note = (
        "\n\n[User attachments: "
        f"{listing}. The files are stored in the workspace 'uploads/' "
        "directory; use your file tools (ls / read_file ...) to inspect "
        "them before answering.]"
    )
    return (message or "") + note


def _ensure_session(
    store: Any,
    session_id: str | None,
    agent_id: str,
) -> str:
    """Return a valid session id, creating one if *session_id* is ``None``."""
    if session_id is not None:
        existing = store.load_session(session_id)
        if existing is not None:
            return session_id
        store.save_session(session_id, {
            "session_id": session_id,
            "agent_id": agent_id,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "messages": [],
        })
        return session_id

    new_id = uuid.uuid4().hex
    store.save_session(new_id, {
        "session_id": new_id,
        "agent_id": agent_id,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "messages": [],
    })
    return new_id


def _append_message(
    store: Any,
    session_id: str,
    role: str,
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    """Append a user/assistant message to the session and persist.

    *extras* are merged into the stored message verbatim (e.g. ``reasoning``,
    ``todos``, ``delegations``, ``approval_request``) so session history is a
    faithful record of the turn.
    """
    session = store.load_session(session_id)
    if session is None:
        return
    messages: list[dict] = session.get("messages", [])
    msg: dict[str, Any] = {
        "role": role,
        "content": content,
        "timestamp": _now_iso(),
    }
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if extras:
        msg.update(extras)
    messages.append(msg)
    session["messages"] = messages
    session["updated_at"] = _now_iso()
    store.save_session(session_id, session)


def _extract_message_reasoning(msg: Any) -> str:
    """Best-effort extraction of reasoning/deliberation text from an AI message.

    Reasoning models (e.g. Qwen3 through ChatOpenAI) surface their chain of
    thought either as an attribute (``reasoning_content``) or inside
    ``additional_kwargs`` — probe both, plus generic ``reasoning`` /
    ``deliberation`` aliases.
    """
    for attr in ("reasoning_content", "reasoning", "deliberation"):
        val = getattr(msg, attr, None)
        if isinstance(val, str) and val:
            return val
    kwargs = getattr(msg, "additional_kwargs", None)
    if isinstance(kwargs, dict):
        for key in ("reasoning_content", "reasoning", "deliberation"):
            val = kwargs.get(key)
            if isinstance(val, str) and val:
                return val
    return ""


def _extract_chunk_reasoning(msg_chunk: Any) -> str:
    """Reasoning extraction for a streaming token chunk (AIMessageChunk)."""
    return _extract_message_reasoning(msg_chunk)


def _extract_chunk_usage(msg_chunk: Any, metadata: Any = None) -> dict[str, int] | None:
    """Extract cumulative token usage from a streaming chunk.

    In LangGraph's ``messages`` stream mode the usage is carried on the
    ``AIMessageChunk`` itself — either via the standard ``usage_metadata``
    attribute (LangChain convention) or in provider-specific spots such as
    ``response_metadata['token_usage']`` / ``additional_kwargs['token_usage']``
    (OpenAI-compatible APIs like DeepSeek). The stream metadata dict is only
    used as a last-resort fallback.
    """
    candidates: list[Any] = []
    for attr in ("usage_metadata", "usage"):
        val = getattr(msg_chunk, attr, None)
        if isinstance(val, dict):
            candidates.append(val)
    resp_meta = getattr(msg_chunk, "response_metadata", None)
    if isinstance(resp_meta, dict) and isinstance(resp_meta.get("token_usage"), dict):
        candidates.append(resp_meta["token_usage"])
    add_kwargs = getattr(msg_chunk, "additional_kwargs", None)
    if isinstance(add_kwargs, dict) and isinstance(add_kwargs.get("token_usage"), dict):
        candidates.append(add_kwargs["token_usage"])
    if isinstance(metadata, dict):
        for key in ("usage", "usage_metadata"):
            val = metadata.get(key)
            if isinstance(val, dict):
                candidates.append(val)

    for usage in candidates:
        inp = usage.get("input_tokens") or usage.get("prompt_tokens")
        out = usage.get("output_tokens") or usage.get("completion_tokens")
        try:
            inp_i, out_i = int(inp or 0), int(out or 0)
        except (TypeError, ValueError):
            continue
        if inp_i or out_i:
            # Debug: log full usage structure to see if reasoning_tokens exists
            logger.debug("Full usage metadata: %s", usage)
            return {"input_tokens": inp_i, "output_tokens": out_i}
    return None


def _collect_turn_metadata(result: Any) -> dict[str, Any]:
    """Extract reasoning, todo plan and delegations from an invocation result.

    Mirrors what the streaming endpoint persists per turn, so synchronous
    chat history carries the same metadata fields.
    """
    if isinstance(result, dict):
        messages = result.get("messages", [])
    elif isinstance(result, list):
        messages = result
    else:
        return {}

    # Narrow to the current turn (same logic as _extract_text_from_result).
    last_human = -1
    for idx, msg in enumerate(messages):
        msg_type = getattr(msg, "type", None)
        if msg_type is None and isinstance(msg, dict):
            msg_type = msg.get("type")
        if msg_type == "human":
            last_human = idx
    if last_human >= 0:
        messages = messages[last_human + 1:]

    reasoning_parts: list[str] = []
    latest_todos: list[dict[str, Any]] | None = None
    delegations: list[dict[str, Any]] = []
    a2ui_surfaces: list[dict[str, Any]] = []

    for msg in messages:
        msg_type = getattr(msg, "type", None)
        if msg_type is None and isinstance(msg, dict):
            msg_type = msg.get("type")
        if msg_type is not None and msg_type != "ai":
            continue

        reasoning = _extract_message_reasoning(msg)
        if reasoning:
            reasoning_parts.append(reasoning)

        tc = getattr(msg, "tool_calls", None)
        if tc is None and isinstance(msg, dict):
            tc = msg.get("tool_calls")
        if not tc:
            continue
        for call in tc:
            if not isinstance(call, dict):
                continue
            name = call.get("name", "")
            args = call.get("args", {}) or {}
            if name == "write_todos" and args.get("todos"):
                latest_todos = args["todos"]
            elif name == "task" and args.get("subagent_type"):
                delegations.append({
                    "subagent": args["subagent_type"],
                    "description": args.get("description", ""),
                })
            elif name == "send_a2ui":
                surface = _a2ui_surface_from_args(args)
                if surface:
                    a2ui_surfaces.append(surface)

    meta: dict[str, Any] = {}
    if reasoning_parts:
        meta["reasoning"] = "\n".join(reasoning_parts)
    if latest_todos:
        meta["todos"] = latest_todos
    if delegations:
        meta["delegations"] = delegations
    if a2ui_surfaces:
        meta["a2ui_surfaces"] = a2ui_surfaces
    return meta


def _mark_last_pending_approval(
    store: Any,
    session_id: str,
    decision: str,
    tool_name: str,
    new_approval_request: dict[str, Any] | None = None,
    clear_request: bool = False,
) -> None:
    """Record an approval decision on the assistant message that requested it.

    The last assistant message carrying an ``approval_request`` gets an
    ``approval`` field describing the decision; when a further interrupt fires
    (*new_approval_request*), the pending request is refreshed in place.  When
    the approval chain finishes (*clear_request*), the stale pending request
    is removed so history does not keep showing an actionable card.
    """
    session = store.load_session(session_id)
    if session is None:
        return
    messages: list[dict] = session.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("approval_request"):
            # Fall back to the persisted request's own action name — the
            # graph-state probe can come back empty after a resume.
            resolved_tool = tool_name
            if not resolved_tool:
                actions = (msg.get("approval_request") or {}).get("actions", [])
                if actions:
                    resolved_tool = actions[0].get("name", "")
            msg["approval"] = {
                "decision": decision,
                "tool_name": resolved_tool,
                "timestamp": _now_iso(),
            }
            if new_approval_request is not None:
                msg["approval_request"] = new_approval_request
            elif clear_request:
                msg.pop("approval_request", None)
            break
    session["updated_at"] = _now_iso()
    store.save_session(session_id, session)


def _extract_usage_from_result(result: Any) -> tuple[int, int]:
    """Sum ``(input_tokens, output_tokens)`` from AI messages' usage metadata.

    LangChain ``AIMessage`` objects carry ``usage_metadata``
    (``{"input_tokens": ..., "output_tokens": ..., "total_tokens": ...}``)
    when the provider reports token counts.  With a checkpointer attached
    the result contains the full thread history, so only messages after the
    last human message (this turn) are counted.
    """
    if isinstance(result, dict):
        messages = result.get("messages", [])
    elif isinstance(result, list):
        messages = result
    else:
        return 0, 0

    last_human = -1
    for idx, msg in enumerate(messages):
        msg_type = getattr(msg, "type", None)
        if msg_type is None and isinstance(msg, dict):
            msg_type = msg.get("type")
        if msg_type == "human":
            last_human = idx
    if last_human >= 0:
        messages = messages[last_human + 1:]

    input_tokens = 0
    output_tokens = 0
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        if msg_type is None and isinstance(msg, dict):
            msg_type = msg.get("type")
        if msg_type is not None and msg_type != "ai":
            continue
        usage = getattr(msg, "usage_metadata", None)
        if usage is None and isinstance(msg, dict):
            usage = msg.get("usage_metadata")
        if not usage:
            continue
        input_tokens += int(usage.get("input_tokens", 0) or 0)
        output_tokens += int(usage.get("output_tokens", 0) or 0)
    return input_tokens, output_tokens


def _extract_text_from_result(result: Any) -> tuple[str, list[dict[str, Any]]]:
    """Extract text content and tool calls from agent output.

    The deepagents SDK (LangGraph) returns ``{"messages": [...]}``.  Only
    ``AI`` messages contribute reply text — human echoes and tool results
    are skipped.  The last message is typically the final ``AIMessage``
    with ``.content`` and optionally ``.tool_calls``.

    With a checkpointer attached, the returned state contains the **full
    thread history**, so only messages produced during the current
    invocation (i.e. after the last human message) belong to this turn's
    reply.
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    if isinstance(result, dict):
        messages = result.get("messages", [])
    elif isinstance(result, list):
        messages = result
    else:
        return str(result), []

    # Narrow to the current turn: drop everything up to and including the
    # last human message (the request we just sent).
    last_human = -1
    for idx, msg in enumerate(messages):
        msg_type = getattr(msg, "type", None)
        if msg_type is None and isinstance(msg, dict):
            msg_type = msg.get("type")
        if msg_type == "human":
            last_human = idx
    if last_human >= 0:
        messages = messages[last_human + 1:]

    for msg in messages:
        # Skip non-AI messages (human input, tool results, system prompts).
        msg_type = getattr(msg, "type", None)
        if msg_type is None and isinstance(msg, dict):
            msg_type = msg.get("type")
        if msg_type is not None and msg_type != "ai":
            continue

        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content", "")
        if isinstance(content, str) and content:
            text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

        tc = getattr(msg, "tool_calls", None)
        if tc is None and isinstance(msg, dict):
            tc = msg.get("tool_calls")
        if tc:
            for call in tc:
                if isinstance(call, dict):
                    tool_calls.append({
                        "name": call.get("name", ""),
                        "args": call.get("args", {}),
                        "id": call.get("id", ""),
                    })

    # A turn can carry several AI messages (a preamble before a tool call
    # plus the final answer); keep them as separate paragraphs.
    return "\n\n".join(text_parts), tool_calls


# ---------------------------------------------------------------------------
# Routes — chat attachment upload
# ---------------------------------------------------------------------------

# Per-file size cap for chat attachments (module-level so tests can patch).
MAX_CHAT_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MiB

# Workspace subdirectory where chat attachments are stored — inside the
# agent workspace so its built-in file tools can read them directly.
UPLOAD_DIR_NAME = "uploads"


def _safe_upload_filename(name: str) -> str:
    """Safe basename: basename only, word chars / dot / dash, max 120."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^\w.\-]", "_", base)[:120]
    return cleaned or "file"


@router.post("/upload")
async def upload_chat_attachment(
    req: Request,
    agent_id: str,
    file: UploadFile = File(..., description="File to attach"),
) -> dict[str, Any]:
    """Upload a chat attachment into the agent workspace's ``uploads/`` dir.

    Mirrors the QwenPaw ``POST /console/upload`` flow, but stores files
    inside the agent's workspace instead of a separate media directory so
    the agent's file tools can read them.  The stored name carries a short
    uuid prefix to avoid collisions; the original name is returned as-is.
    """
    manager = getattr(req.app.state, "agent_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503, detail="MultiAgentManager is not available."
        )

    # Reject unknown agents instead of silently creating a workspace
    # (same resolution as files_router._get_workspace).
    workspace = manager.get_workspace(agent_id)
    if workspace is None:
        store = getattr(req.app.state, "store", None)
        known = store is not None and agent_id in getattr(
            store, "agent_states", {}
        )
        if not known:
            raise HTTPException(
                status_code=404, detail=f"Agent {agent_id} not found"
            )
        workspace = await manager.get_or_create_workspace(agent_id)

    data = await file.read()
    if len(data) > MAX_CHAT_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large ({len(data)} bytes); "
                f"limit is {MAX_CHAT_UPLOAD_BYTES} bytes"
            ),
        )

    safe_name = _safe_upload_filename(file.filename or "file")
    stored_name = f"{uuid.uuid4().hex[:12]}_{safe_name}"
    rel_path = f"{UPLOAD_DIR_NAME}/{stored_name}"
    target = workspace.workspace_dir / rel_path

    def _write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    try:
        await asyncio.to_thread(_write)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to store upload: {exc}"
        ) from exc

    logger.info(
        "chat upload[%s]: %s -> %s (%d bytes)",
        agent_id,
        safe_name,
        rel_path,
        len(data),
    )
    return {"name": safe_name, "path": rel_path, "size": len(data)}


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest, req: Request) -> ChatResponse:
    """Synchronous chat — send a message, wait for the full response.

    The agent is created via the shared
    :class:`~agentcore.runtime.agent_factory.AgentFactory` using the
    ``agent.json`` configuration stored in the agent's workspace.
    """
    store = _get_store(req)

    # Zombie-resurrection guard: reject chats against a deleted agent
    # BEFORE we touch ``_ensure_session`` / ``get_or_create_workspace``,
    # both of which would otherwise recreate the session record and the
    # workspace directory from scratch (this was the primary path by
    # which deleted agents kept coming back — a stale browser tab would
    # hit ``/api/chat`` with a cached ``agent_id`` and the write side
    # would silently undo the earlier DELETE).
    if request.agent_id not in known_agent_ids(req.app.state):
        raise HTTPException(
            status_code=404,
            detail=f"Agent {request.agent_id!r} not found",
        )

    session_id = _ensure_session(store, request.session_id, request.agent_id)

    # Persist user message (attachments ride along as message extras).
    _append_message(
        store,
        session_id,
        "user",
        request.message,
        extras=_attachment_extras(request.attachments),
    )

    # Resolve the agent's workspace so the agent.json config and kernel
    # files (agent.md / memory files) are available.
    workspace = None
    manager = getattr(req.app.state, "agent_manager", None)
    if manager is not None:
        try:
            workspace = await manager.get_or_create_workspace(request.agent_id)
        except Exception:
            logger.exception(
                "Failed to load workspace for agent %s",
                request.agent_id,
            )

    # Resolve agent graph from the workspace config (cached, factory-built).
    # The graph cache lives on the app-scoped ChatState (app.state).
    factory = getattr(req.app.state, "factory", None)
    chat_state = get_chat_state(req.app.state)

    # Load agent config early for token recording and sandbox management
    agent_config = {}
    if workspace is not None:
        try:
            agent_config = workspace.read_agent_config()
        except Exception:
            logger.exception("Failed to read agent config for %s", request.agent_id)

    # --- Sandbox session management ---
    # If the agent uses sandbox, ensure the session is active before
    # resolving the graph. The session manager handles lifecycle
    # (create/renew/resume) based on the configured strategy.
    sandbox_backend = None
    sandbox_recreated = False  # Track if sandbox was recreated due to death
    sandbox_mgr = getattr(req.app.state, "sandbox_session_manager", None)
    if sandbox_mgr is not None and workspace is not None:
        try:
            backend_cfg = (agent_config.get("settings") or {}).get("backend", {})
            if backend_cfg.get("type") == "sandbox":
                session = await sandbox_mgr.get_or_create(
                    request.agent_id, backend_cfg
                )
                if session is not None:
                    sandbox_backend = session.backend
                    sandbox_recreated = getattr(session, "_sandbox_recreated", False)
                    logger.info(
                        "Sandbox session ready for agent %s (id=%s, status=%s, recreated=%s)",
                        request.agent_id,
                        session.sandbox_id,
                        session.status,
                        sandbox_recreated,
                    )
        except Exception:
            logger.exception(
                "Failed to get/create sandbox session for agent %s",
                request.agent_id,
            )

    # If sandbox was recreated, invalidate graph cache to force
    # creation of a new graph with the new backend
    if sandbox_recreated and chat_state is not None:
        chat_state.graph_cache.pop(request.agent_id, None)
        logger.info(
            "Invalidated graph cache for agent %s (sandbox recreated)",
            request.agent_id,
        )

    # --- Sandbox workspace sync -----------------------------------------
    # Upload local workspace files (skills, memory, kernel) to the sandbox
    # container so the SDK's middleware can discover them through the backend.
    if sandbox_backend is not None and workspace is not None:
        try:
            await _sync_workspace_to_sandbox(workspace, sandbox_backend)
        except Exception:
            logger.exception(
                "Failed to sync workspace to sandbox for agent %s",
                request.agent_id,
            )

    agent_graph = await _resolve_agent_graph(
        request.agent_id,
        workspace,
        factory=factory,
        manager=manager,
        chat_state=chat_state,
        sandbox_backend=sandbox_backend,
    )

    # Track the session thread so invalidation can purge checkpointed
    # memory/skills state (they are loaded once per thread by the SDK).
    chat_state.threads.setdefault(request.agent_id, set()).add(session_id)

    # Bootstrap closed loop: remember whether bootstrap.md exists before
    # the invocation.  If the agent deletes it during this turn (via the
    # delete tool, as bootstrap.md instructs), the graph cache is
    # invalidated afterwards so the next chat rebuilds the agent without
    # the first-run guidance in its system_prompt.
    bootstrap_path = (
        workspace.workspace_dir / BOOTSTRAP_MD_NAME if workspace is not None else None
    )
    bootstrap_existed_before = bool(bootstrap_path and bootstrap_path.exists())

    # Invoke the agent.  The session_id doubles as the LangGraph thread_id
    # so the checkpointer replays prior conversation turns into the model.
    await mark_turn_begin(req.app.state, request.agent_id)
    try:
        input_data = {
            "messages": [
                HumanMessage(
                    content=_with_attachment_note(
                        request.message, request.attachments
                    )
                )
            ]
        }
        result = await agent_graph.ainvoke(
            input_data,
            config={"configurable": {"thread_id": session_id}},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Agent %s invocation failed", request.agent_id)
        raise HTTPException(
            status_code=500,
            detail=f"Agent invocation failed: {exc}",
        ) from exc
    finally:
        await mark_turn_end(req.app.state, request.agent_id)

    # --- HITL interrupt detection (Task 1.2) --------------------------
    # If the graph paused due to a HITL interrupt, surface the approval
    # request instead of a normal reply.
    interrupts = _extract_interrupts_from_result(result)
    if interrupts:
        approval_info = _build_approval_request_info(interrupts)
        logger.info(
            "Agent %s: HITL interrupt detected in session %s — %d action(s) "
            "pending approval",
            request.agent_id,
            session_id,
            len(approval_info.get("actions", [])),
        )
        # Persist the pending approval so session history shows the request.
        _append_message(
            store, session_id, "assistant", "",
            extras={"approval_request": approval_info},
        )
        return ChatResponse(
            session_id=session_id,
            agent_id=request.agent_id,
            content="",
            timestamp=_now_iso(),
            status="pending_approval",
            approval_request=approval_info,
        )

    content, tool_calls = _extract_text_from_result(result)
    content = _collapse_blank_runs(content)

    # Bootstrap closed loop (second half): bootstrap.md disappeared during
    # this turn ⇒ initial setup finished ⇒ drop the cached graph (which
    # still carries the guidance in its system_prompt / checkpoint state).
    # Best-effort only — never break the chat flow.
    if bootstrap_existed_before and not bootstrap_path.exists():
        try:
            await invalidate_agent_graph(req.app.state, request.agent_id)
            logger.info(
                "Agent %s: bootstrap.md removed during chat — agent graph "
                "invalidated",
                request.agent_id,
            )
        except Exception:
            logger.exception(
                "Agent %s: failed to invalidate graph after bootstrap "
                "removal",
                request.agent_id,
            )

    # Record token consumption (best-effort; never breaks the chat flow).
    input_tokens, output_tokens = _extract_usage_from_result(result)
    if input_tokens or output_tokens:
        from agentcore.runtime.token_router import record_token_usage
        from agentcore.runtime.model_factory import build_model_string

        _model_str = build_model_string(agent_config.get("model")) or ""
        record_token_usage(request.agent_id, input_tokens, output_tokens, _model_str)

    # Persist assistant reply with the turn's metadata (reasoning, todo
    # plan, delegations) so history is complete for sync chat too.
    extras = _collect_turn_metadata(result)
    _append_message(
        store, session_id, "assistant", content, tool_calls or None,
        extras=extras or None,
    )

    # Phase 3: Archive this turn's messages to the daily memory archive.
    # Best-effort only — never breaks the chat flow.
    if workspace is not None:
        try:
            from datetime import datetime as _dt
            date_str = _dt.now(tz=timezone.utc).strftime("%Y-%m-%d")
            workspace.archive_session_messages(
                date_str,
                request.agent_id,
                [
                    {"role": "user", "content": request.message, "timestamp": _now_iso()},
                    {"role": "assistant", "content": content, "timestamp": _now_iso()},
                ],
            )
        except Exception:
            logger.warning(
                "Failed to archive session messages for agent %s",
                request.agent_id,
                exc_info=True,
            )

    return ChatResponse(
        session_id=session_id,
        agent_id=request.agent_id,
        content=content,
        timestamp=_now_iso(),
        tool_calls=tool_calls,
    )


# ---------------------------------------------------------------------------
# Streaming SSE endpoint (Tasks 3.1, 3.2)
# ---------------------------------------------------------------------------


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a single SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


# ---------------------------------------------------------------------------
# A2UI (Agent-to-User Interface) projection helpers
# ---------------------------------------------------------------------------

# Matches ``basicCatalog.id`` in @a2ui/web_core v0_9 — the client and the
# server must agree on this identifier for surfaces to render.
A2UI_BASIC_CATALOG_ID = (
    "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
)


def _normalize_a2ui_action(action: Any) -> Any:
    """Repair common LLM mis-shapings of a Button's ``action`` property.

    The spec requires ``{"event": {"name": ..., "context": ...}}`` but models
    sometimes emit ``name``/``context`` at the top level, or ``context`` as a
    sibling of ``event``.  In that shape the client runtime reads an empty
    context on click (``payload.event.context``), silently dropping the user's
    form data.  Hoist the stray keys into the ``event`` envelope.
    """
    if not isinstance(action, dict):
        return action
    if "name" not in action and "context" not in action:
        return action
    fixed = dict(action)
    event = fixed.get("event")
    event = dict(event) if isinstance(event, dict) else {}
    for key in ("name", "context"):
        if key in fixed:
            event.setdefault(key, fixed.pop(key))
    fixed["event"] = event
    return fixed


def _normalize_a2ui_component(comp: dict[str, Any]) -> None:
    """Repair common LLM mis-shapings in a single component (in place).

    - ``action``: hoist stray top-level ``name``/``context`` into the
      ``event`` envelope (see :func:`_normalize_a2ui_action`).
    - ``Row``/``Column``: models habitually emit CSS values such as
      ``flex-end`` for ``justify``/``align`` which fail the catalog's
      camelCase enums — map them onto the legal values.
    """
    if "action" in comp:
        comp["action"] = _normalize_a2ui_action(comp["action"])
    if comp.get("component") in ("Row", "Column"):
        for key, mapping in (
            ("justify", _A2UI_JUSTIFY_ALIASES),
            ("align", _A2UI_ALIGN_ALIASES),
        ):
            val = comp.get(key)
            if isinstance(val, str) and val in mapping:
                comp[key] = mapping[val]


# CSS-style values LLMs commonly emit → legal A2UI basic-catalog enums.
_A2UI_JUSTIFY_ALIASES = {
    "flex-start": "start",
    "flex-end": "end",
    "space-between": "spaceBetween",
    "space-around": "spaceAround",
    "space-evenly": "spaceEvenly",
}
_A2UI_ALIGN_ALIASES = {
    "flex-start": "start",
    "flex-end": "end",
    "baseline": "start",
    "normal": "stretch",
}


def _a2ui_surface_from_args(args: Any) -> dict[str, Any] | None:
    """Build an A2UI surface payload from ``send_a2ui`` tool-call args.

    Assembles the v0.9 envelope sequence (``createSurface`` /
    ``updateComponents`` / optional ``updateDataModel``) that the client's
    ``MessageProcessor`` expects.  Returns ``None`` when the payload is
    unusable (missing/invalid components) so a malformed LLM output
    degrades to a plain tool card instead of breaking the stream.
    """
    if not isinstance(args, dict):
        return None
    components = args.get("components")
    if not isinstance(components, list) or not components:
        return None
    components = [dict(c) for c in components if isinstance(c, dict) and c.get("id")]
    for comp in components:
        _normalize_a2ui_component(comp)
    if not components:
        return None

    surface_id = f"surf-{uuid.uuid4().hex[:10]}"
    messages: list[dict[str, Any]] = [
        {
            "version": "v0.9",
            "createSurface": {
                "surfaceId": surface_id,
                "catalogId": A2UI_BASIC_CATALOG_ID,
                "sendDataModel": True,
            },
        },
        {
            "version": "v0.9",
            "updateComponents": {
                "surfaceId": surface_id,
                "components": components,
            },
        },
    ]
    data = args.get("data")
    if isinstance(data, dict) and data:
        messages.append({
            "version": "v0.9",
            "updateDataModel": {
                "surfaceId": surface_id,
                "path": "/",
                "value": data,
            },
        })
    return {
        "surface_id": surface_id,
        "title": str(args.get("surface_title") or ""),
        "messages": messages,
    }


def _collapse_blank_runs(text: str) -> str:
    """Trim surrounding whitespace and clamp 3+ newlines to one blank line.

    This is deliberately *not* a text rewriter.  Assistant replies are stored
    exactly as they were streamed, so a reloaded session renders identically
    to the live answer; markdown structure (single-newline list items, table
    rows, hard breaks) must survive untouched.  Only blank runs — which
    markdown collapses anyway — are tidied away.
    """
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _append_assistant_turn(
    store: Any,
    session_id: str,
    text_parts: list[str],
    reasoning_parts: list[str],
    tool_calls: list[dict[str, Any]],
    latest_todos: list[dict[str, Any]] | None,
    delegations: list[dict[str, Any]],
    latest_approval: dict[str, Any] | None,
    token_usage: dict[str, int] | None = None,
    a2ui_surfaces: list[dict[str, Any]] | None = None,
) -> None:
    """Persist one streaming turn as a single assistant message.

    Bundles the reply text, reasoning chain, tool calls, todo plan,
    delegations, approval request, A2UI surfaces and token consumption
    into the session history.
    """
    extras: dict[str, Any] = {}
    # ``text_parts`` / ``reasoning_parts`` are *stream deltas*: joining them
    # with any separator (or rewriting their newlines) makes the persisted
    # reply differ from what the user just watched render — for CJK models it
    # injects a space at every token boundary and breaks markdown.  Concatenate
    # verbatim so history is a faithful record of the turn.
    text = "".join(text_parts).strip()
    reasoning = "".join(reasoning_parts).strip()
    if reasoning:
        extras["reasoning"] = reasoning
    if latest_todos:
        extras["todos"] = latest_todos
    if delegations:
        extras["delegations"] = delegations
    if latest_approval:
        extras["approval_request"] = latest_approval
    if token_usage and (token_usage.get("input_tokens") or token_usage.get("output_tokens")):
        extras["token_usage"] = token_usage
    if a2ui_surfaces:
        extras["a2ui_surfaces"] = a2ui_surfaces
    _append_message(
        store, session_id, "assistant",
        text,
        tool_calls or None,
        extras=extras,
    )


def _friendly_stream_error(exc: BaseException) -> str:
    """Render a user-friendly error message for stream failures.

    Provider throttling (HTTP 429 / rate-limit) gets a readable hint;
    everything else keeps the original detail for debugging.
    """
    text = str(exc)
    if (
        getattr(exc, "status_code", None) == 429
        or "RateLimitError" in type(exc).__name__
        or "429" in text
    ):
        # OpenCode Zen free-tier models surface ``FreeUsageLimitError``:
        # a provider-side rate limit on free usage, not account balance.
        if "FreeUsageLimitError" in text:
            return (
                "Stream error: 免费模型触发服务商限流（FreeUsageLimitError，429），"
                "请稍后再试。这与账户余额无关，是免费额度的速率限制。"
            )
        return (
            "Stream error: 模型服务配额已用尽（429），请稍后再试，"
            "或检查模型账户余额/额度配置。"
        )
    return f"Stream error: {text}"


class _SubagentActivityCollector(BaseCallbackHandler):
    """Surface subagent internal activity through LangChain callbacks.

    The deepagents ``task`` tool invokes the subagent graph *inside* a tool
    execution, so the parent's ``astream`` modes (updates/messages/values)
    stay silent for the whole delegation.  LangChain propagates this
    handler (and the ``lc_agent_name`` metadata the subagent's runnable is
    bound with) down to every child run, which gives us:

    * ``task`` tool start/end  → the *real* delegation lifecycle (replaces
      the fake "parent stream is open" running indicator on the frontend)
    * tools / chat-model calls inside the subagent → a live progress feed

    Events are pushed onto an asyncio queue drained by the SSE generator
    via :func:`_merge_stream_sources`, and accumulated per subagent name
    so the delegation can be persisted with its full activity timeline.
    """

    def __init__(self) -> None:
        self.queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
        # Subagent names observed from ``task`` tool starts — used to
        # recognise child runs via their inherited ``lc_agent_name``.
        self.subagent_names: set[str] = set()
        # agent name → ordered activity entries (for persistence).
        self.activity: dict[str, list[dict[str, Any]]] = {}
        # run_id bookkeeping for lifecycle correlation.
        self._task_runs: dict[str, str] = {}   # task run → agent name
        self._tool_runs: dict[str, tuple[str, str]] = {}  # tool run → (agent, tool)

    # -- helpers ---------------------------------------------------------

    def _emit(self, agent: str, kind: str, name: str = "") -> None:
        entry: dict[str, Any] = {
            "agent": agent,
            "kind": kind,  # started | tool | tool_done | thinking | completed | error
            "name": name,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.activity.setdefault(agent, []).append(entry)
        try:
            self.queue.put_nowait(entry)
        except Exception:  # pragma: no cover — queue is unbounded
            pass

    def _subagent_of(self, metadata: Any) -> str | None:
        name = (metadata or {}).get("lc_agent_name")
        if isinstance(name, str) and name in self.subagent_names:
            return name
        return None

    # -- callback hooks --------------------------------------------------

    def on_tool_start(
        self, serialized, input_str, *, run_id, parent_run_id=None,
        tags=None, metadata=None, inputs=None, **kwargs,
    ) -> None:
        tool_name = (serialized or {}).get("name") or ""
        if tool_name == "task":
            sub = ""
            if isinstance(inputs, dict):
                sub = str(inputs.get("subagent_type") or "")
            if not sub:
                try:
                    sub = str(json.loads(input_str or "{}").get("subagent_type") or "")
                except (json.JSONDecodeError, TypeError, ValueError):
                    sub = ""
            if sub:
                self.subagent_names.add(sub)
                self._task_runs[str(run_id)] = sub
                self._emit(sub, "started")
            return
        agent = self._subagent_of(metadata)
        if agent:
            self._tool_runs[str(run_id)] = (agent, tool_name)
            self._emit(agent, "tool", tool_name)

    def on_tool_end(self, output, *, run_id, parent_run_id=None, tags=None,
                    metadata=None, **kwargs) -> None:
        rid = str(run_id)
        sub = self._task_runs.pop(rid, None)
        if sub is not None:
            self._emit(sub, "completed")
            return
        agent_tool = self._tool_runs.pop(rid, None)
        if agent_tool is not None:
            self._emit(agent_tool[0], "tool_done", agent_tool[1])

    def on_tool_error(self, error, *, run_id, parent_run_id=None, tags=None,
                      metadata=None, **kwargs) -> None:
        rid = str(run_id)
        sub = self._task_runs.pop(rid, None)
        if sub is not None:
            self._emit(sub, "error")
            return
        agent_tool = self._tool_runs.pop(rid, None)
        if agent_tool is not None:
            self._emit(agent_tool[0], "error", agent_tool[1])

    def on_chat_model_start(self, serialized, messages, *, run_id,
                            parent_run_id=None, tags=None, metadata=None,
                            **kwargs) -> None:
        agent = self._subagent_of(metadata)
        if agent:
            self._emit(agent, "thinking")


def _trim_activity(acts: "list[dict[str, Any]]", cap: int) -> "list[dict[str, Any]]":
    """Cap a persisted activity timeline while keeping its terminal event.

    A long delegation can emit hundreds of steps; store at most *cap* of
    them, but never drop the trailing ``completed``/``error`` marker (the
    frontend's running-state fallback relies on it) — when trimming, the
    middle is collapsed into a single ``+N`` summary row.
    """
    if len(acts) <= cap:
        return list(acts)
    tail = acts[-1]
    kept = acts[: cap - 2]
    dropped = len(acts) - 1 - len(kept)
    kept.append({"kind": "collapsed", "agent": tail.get("agent", ""), "name": f"+{dropped}"})
    kept.append(tail)
    return kept


async def _merge_stream_sources(
    primary: "AsyncGenerator[Any, None]",
    activity: "asyncio.Queue[dict[str, Any]]",
) -> "AsyncGenerator[tuple[str, Any], None]":
    """Interleave graph chunks with subagent activity events.

    Yields ``("chunk", x)`` for every item of *primary* (the agent graph's
    ``astream``) and ``("activity", e)`` for events pushed by a
    :class:`_SubagentActivityCollector`.  The primary iterator runs as a
    task so activity surfaces even while the graph is blocked inside a
    long-running tool node (a ``task`` delegation emits no astream chunks
    at all — without this merge the live feed would arrive in one burst
    after the delegation finished).

    Exceptions from *primary* propagate to the caller; on normal
    completion the remaining queued activity is drained first.
    """
    chunk_task: "asyncio.Future[Any] | None" = None
    act_task: "asyncio.Future[Any] | None" = None
    try:
        while True:
            if chunk_task is None:
                chunk_task = asyncio.ensure_future(anext(primary))
            if act_task is None:
                act_task = asyncio.ensure_future(activity.get())
            done, _ = await asyncio.wait(
                {chunk_task, act_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if act_task is not None and act_task in done:
                yield ("activity", act_task.result())
                act_task = None
                continue
            if chunk_task is not None and chunk_task in done:
                try:
                    chunk = chunk_task.result()
                except StopAsyncIteration:
                    chunk_task = None
                    while not activity.empty():
                        yield ("activity", activity.get_nowait())
                    return
                chunk_task = None
                yield ("chunk", chunk)
    finally:
        for _t in (chunk_task, act_task):
            if _t is not None and not _t.done():
                _t.cancel()


async def _stream_chat_sse(
    agent_graph: Any,
    session_id: str,
    agent_id: str,
    message: str,
    chat_state: ChatState,
    store: Any = None,
    workspace: Any = None,
    app_state: Any = None,
) -> AsyncGenerator[str, None]:
    """Generate SSE events from an agent stream.

    Five projection channels are emitted:

    * **messages** — token-by-token LLM output (``stream_mode="messages"``)
    * **subagents** — node updates showing subagent delegation
      (``stream_mode="updates"``)
    * **todo** — structured task plan updates from ``write_todos`` calls
    * **interrupts** — HITL interrupt events (``stream_mode="values"``)
    * **token_usage** — cumulative token counts extracted from message
      metadata

    *store* (when provided) is used to persist delegation records so a
    subagent's own workspace can later show who delegated what to it.
    """
    input_data = {"messages": [HumanMessage(content=message)]}
    config = {"configurable": {"thread_id": session_id}}

    # Subagent activity collector: attached to the run config so its
    # callbacks propagate into ``task``-tool subagent invocations; the
    # merged stream below surfaces those events as ``subagent_activity``
    # SSE chunks while the graph itself is silent inside the delegation.
    collector = _SubagentActivityCollector()
    config = {**config, "callbacks": [collector]}

    # Track the session thread for invalidation purposes.
    chat_state.threads.setdefault(agent_id, set()).add(session_id)

    # Bookkeeping: the agent is busy for the lifetime of this stream.
    if app_state is not None:
        await mark_turn_begin(app_state, agent_id)

    total_input = 0
    total_output = 0

    # Turn-level collectors — persisted as one assistant message at the end
    # so session history keeps the reply, reasoning, tool calls, todo plan,
    # delegations and approval requests of every turn.
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    latest_todos: list[dict[str, Any]] | None = None
    delegations: list[dict[str, Any]] = []
    latest_approval: dict[str, Any] | None = None
    # A2UI interactive surfaces rendered this turn (projected from
    # ``send_a2ui`` calls and persisted alongside the reply).
    a2ui_surfaces: list[dict[str, Any]] = []
    # Dedup guard: a send_a2ui call is projected exactly once even though
    # it can surface in both ``messages`` and ``updates`` stream modes.
    projected_a2ui_keys: set[str] = set()
    # Track the index of the currently in-progress todo for real-time status updates.
    current_todo_idx: int = -1
    # When the agent manages todo statuses itself (write_todos calls carry
    # non-pending statuses), disable the heuristic auto-advance to avoid
    # conflicting updates.
    agent_managed_todos = False

    # Pre-compute the set of tools requiring approval so we can detect
    # interrupts early in ``updates`` mode — before the ``values`` mode
    # fires its (delayed) ``__interrupt__`` event.  This ensures the
    # approval card appears immediately when tool calls are detected,
    # not after the full LLM reply has been streamed.
    approval_tool_names: set[str] = set()
    if workspace is not None:
        try:
            from agentcore.runtime.security_router import apply_global_approval
            _cfg = workspace.read_agent_config()
            _settings = _cfg.get("settings", {}) or {}
            _merged = apply_global_approval(_settings)
            for _rule in (_merged.get("interrupt_rules") or []):
                if _rule.get("require_approval") and _rule.get("tool_name"):
                    approval_tool_names.add(_rule["tool_name"])
        except Exception:
            logger.debug("Failed to pre-read interrupt_rules for %s", agent_id)

    # Streaming tool-call accumulator — tool calls arrive as fragmented
    # chunks; we merge them by index and only emit an early ``tool_calls``
    # event once the arguments form valid JSON (the call is complete).
    pending_tool_chunks: dict[int, dict[str, Any]] = {}
    emitted_tool_call_keys: set[str] = set()

    def _process_tool_call_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge tool-call chunk deltas; return calls that became complete."""
        nonlocal latest_todos, current_todo_idx, agent_managed_todos
        completed: list[dict[str, Any]] = []
        for tc in chunks:
            if not isinstance(tc, dict):
                continue
            idx = tc.get("index") or 0
            acc = pending_tool_chunks.setdefault(
                idx, {"name": "", "args": "", "id": None}
            )
            if tc.get("name"):
                acc["name"] = tc["name"]
            if tc.get("id"):
                acc["id"] = tc["id"]
            acc["args"] += tc.get("args") or ""

            key = acc["id"] or f"{acc['name']}@{idx}"
            if key in emitted_tool_call_keys:
                continue
            try:
                parsed_args = json.loads(acc["args"]) if acc["args"] else None
            except (json.JSONDecodeError, ValueError):
                continue
            if parsed_args is None or not acc["name"]:
                continue
            emitted_tool_call_keys.add(key)
            completed.append({"name": acc["name"], "args": parsed_args})

            # write_todos is projected as a todo event, not a tool card.
            if acc["name"] == "write_todos":
                todos = parsed_args.get("todos", []) if isinstance(parsed_args, dict) else []
                if todos:
                    if any(t.get("status") not in (None, "pending") for t in todos):
                        agent_managed_todos = True
                    if not agent_managed_todos:
                        for todo in todos:
                            if todo.get("status") == "pending":
                                todo["status"] = "in_progress"
                                current_todo_idx = todos.index(todo)
                                break
                    latest_todos = todos
        return completed

    persisted = False

    def _advance_todo_status(completed_tool_name: str | None = None) -> list[dict[str, Any]] | None:
        """Advance todo status based on tool execution.

        When a tool completes, mark the current in-progress todo as completed
        and move to the next pending todo. Returns the updated todos list.
        """
        nonlocal latest_todos, current_todo_idx
        if agent_managed_todos or not latest_todos:
            return None

        # Mark current in-progress todo as completed.
        if 0 <= current_todo_idx < len(latest_todos):
            latest_todos[current_todo_idx]["status"] = "completed"

        # Find next pending todo and mark it as in_progress.
        next_idx = -1
        for i, todo in enumerate(latest_todos):
            if todo.get("status") == "pending":
                next_idx = i
                break

        if next_idx >= 0:
            latest_todos[next_idx]["status"] = "in_progress"
            current_todo_idx = next_idx
        else:
            current_todo_idx = -1

        return latest_todos

    def _persist_partial() -> None:
        """Persist the collected turn so far — idempotent.

        Called on the happy path (full turn), on stream errors and from
        ``finally`` when the client aborts / stops the conversation so
        the session history keeps whatever was already generated.
        """
        nonlocal persisted
        if persisted or store is None:
            return
        if not (text_parts or tool_calls or reasoning_parts
                or latest_todos or delegations or latest_approval
                or a2ui_surfaces):
            return
        # Attach the live subagent activity timeline to each delegation
        # entry so history replay can render what the subagent actually
        # did (best-effort; merged when one agent is delegated to twice).
        for _d in delegations:
            _acts = collector.activity.get(_d.get("subagent", ""))
            if _acts:
                _d["activity"] = _trim_activity(_acts, 80)
                _d["running"] = False
        try:
            _append_assistant_turn(
                store, session_id, text_parts, reasoning_parts,
                tool_calls, latest_todos, delegations, latest_approval,
                token_usage={"input_tokens": total_input, "output_tokens": total_output},
                a2ui_surfaces=a2ui_surfaces or None,
            )
            persisted = True
        except Exception:
            logger.exception(
                "Failed to persist assistant message for session %s",
                session_id,
            )

    try:
        async for item in _merge_stream_sources(
            agent_graph.astream(
                input_data,
                config=config,
                stream_mode=["updates", "messages", "values"],
            ),
            collector.queue,
        ):
            if item[0] == "activity":
                yield _sse_event("subagent_activity", {
                    **item[1],
                    "agent_id": agent_id,
                })
                continue
            chunk = item[1]
            # When multiple stream_modes are passed, each chunk is a
            # tuple of (mode, data).
            if not isinstance(chunk, (list, tuple)) or len(chunk) < 2:
                continue

            mode, data = chunk[0], chunk[1]

            if mode == "messages":
                # Token-by-token LLM output.
                # data is typically (message_chunk, metadata) tuple.
                if isinstance(data, (list, tuple)) and len(data) >= 1:
                    msg_chunk = data[0]

                    # Only stream content from AI messages — skip ToolMessage
                    # and other non-AI messages whose content (e.g. file read
                    # results) would flood the chat with raw tool output.
                    msg_type = getattr(msg_chunk, "type", None)
                    if msg_type is None and isinstance(msg_chunk, dict):
                        msg_type = msg_chunk.get("type")
                    is_ai_content = msg_type not in ("tool", "human", "chat")

                    content = ""
                    if hasattr(msg_chunk, "content"):
                        content = msg_chunk.content or ""
                    elif isinstance(msg_chunk, dict):
                        content = msg_chunk.get("content", "")
                    if content and is_ai_content:
                        text_parts.append(content)
                        yield _sse_event("messages", {
                            "content": content,
                            "agent_id": agent_id,
                        })

                    # Reasoning/deliberation tokens (Qwen3-style models).
                    reasoning = _extract_chunk_reasoning(msg_chunk)
                    if reasoning:
                        reasoning_parts.append(reasoning)
                        yield _sse_event("messages", {
                            "reasoning": reasoning,
                            "agent_id": agent_id,
                        })

                    # Early tool_calls detection — tool calls stream in as
                    # fragmented chunks; merge them and emit only when the
                    # arguments are complete (valid JSON) to avoid duplicate
                    # partial cards on the frontend.
                    raw_chunks = getattr(msg_chunk, "tool_call_chunks", None)
                    if raw_chunks is None and isinstance(msg_chunk, dict):
                        raw_chunks = msg_chunk.get("tool_call_chunks")
                    if raw_chunks:
                        completed_calls = _process_tool_call_chunks(list(raw_chunks))

                        # Early interrupt: if any completed tool call
                        # requires approval, emit the interrupt event
                        # immediately — before the tool_calls event —
                        # so the approval card appears without the tool
                        # card showing first.
                        if approval_tool_names and completed_calls:
                            _early_approval_calls = [
                                c for c in completed_calls
                                if c["name"] in approval_tool_names
                            ]
                            if _early_approval_calls:
                                _early_actions = []
                                for _ea in _early_approval_calls:
                                    _ea_args = _ea.get("args", {})
                                    if isinstance(_ea_args, str):
                                        try:
                                            _ea_args = json.loads(_ea_args)
                                        except (json.JSONDecodeError, ValueError):
                                            _ea_args = {}
                                    _early_actions.append({
                                        "name": _ea.get("name", ""),
                                        "args": _ea_args if isinstance(_ea_args, dict) else {},
                                        "description": f"{_ea.get('name', '')}({json.dumps(_ea_args, ensure_ascii=False)[:120]})",
                                    })
                                _early_approval_info = {"actions": _early_actions}
                                latest_approval = _early_approval_info
                                yield _sse_event("interrupts", {
                                    "status": "pending_approval",
                                    "approval_request": _early_approval_info,
                                    "agent_id": agent_id,
                                    "session_id": session_id,
                                })
                                return

                        # write_todos / send_a2ui are projected as
                        # dedicated events, not tool cards.
                        card_calls = [
                            c for c in completed_calls
                            if c["name"] not in ("write_todos", "send_a2ui")
                        ]
                        if card_calls:
                            yield _sse_event("tool_calls", {
                                "tool_calls": card_calls,
                                "agent_id": agent_id,
                            })
                        for call in completed_calls:
                            if call["name"] == "send_a2ui":
                                surface = _a2ui_surface_from_args(call["args"])
                                if surface:
                                    try:
                                        _key = json.dumps(
                                            call["args"], sort_keys=True, ensure_ascii=False
                                        )
                                    except (TypeError, ValueError):
                                        _key = ""
                                    if _key and _key in projected_a2ui_keys:
                                        continue
                                    if _key:
                                        projected_a2ui_keys.add(_key)
                                    a2ui_surfaces.append(surface)
                                    yield _sse_event("a2ui", {
                                        **surface,
                                        "agent_id": agent_id,
                                    })
                        for call in completed_calls:
                            if call["name"] == "write_todos":
                                args = call["args"]
                                todos_payload = (
                                    args.get("todos", [])
                                    if isinstance(args, dict) else []
                                )
                                if todos_payload:
                                    yield _sse_event("todo", {
                                        "todos": todos_payload,
                                        "agent_id": agent_id,
                                    })

                    # Extract token usage from the chunk itself (LangGraph
                    # carries usage on the AIMessageChunk, not the stream
                    # metadata) — fall back to the metadata dict.
                    usage = _extract_chunk_usage(
                        msg_chunk, data[1] if len(data) > 1 else None
                    )
                    if usage:
                        total_input += usage["input_tokens"]
                        total_output += usage["output_tokens"]
                        yield _sse_event("token_usage", {
                            "input_tokens": total_input,
                            "output_tokens": total_output,
                            "agent_id": agent_id,
                        })

            elif mode == "updates":
                # Node updates — includes subagent delegation events.
                if isinstance(data, dict):
                    for node_name, node_output in data.items():
                        # Detect subagent task delegation.
                        if node_name == "task" or "subagent" in node_name.lower():
                            yield _sse_event("subagents", {
                                "node": node_name,
                                "update": _serialise_node_output(node_output),
                                "agent_id": agent_id,
                            })
                        # Also surface tool execution updates.
                        if isinstance(node_output, dict):
                            msgs = node_output.get("messages", [])
                            for m in msgs:
                                tc = getattr(m, "tool_calls", None) or (
                                    m.get("tool_calls") if isinstance(m, dict) else None
                                )
                                if tc:
                                    calls = [
                                        {
                                            "name": c.get("name", "") if isinstance(c, dict) else getattr(c, "name", ""),
                                            "args": c.get("args", {}) if isinstance(c, dict) else getattr(c, "args", {}),
                                        }
                                        for c in tc
                                    ]
                                    # Keep every tool call of this turn for the
                                    # persisted assistant message.
                                    tool_calls.extend(calls)
                                    # Persist task delegation so the target
                                    # subagent can later see who delegated
                                    # what to it.
                                    for call in calls:
                                        if call.get("name") == "task":
                                            args = call.get("args", {}) or {}
                                            subagent_type = args.get("subagent_type", "")
                                            if subagent_type:
                                                delegations.append({
                                                    "subagent": subagent_type,
                                                    "description": args.get("description", ""),
                                                })
                                            if subagent_type and store is not None:
                                                try:
                                                    store.append_delegation(subagent_type, {
                                                        "parent_agent_id": agent_id,
                                                        "task_description": args.get("description", ""),
                                                        "args": args,
                                                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                                                    })
                                                except Exception:
                                                    logger.exception(
                                                        "Failed to persist delegation to %s",
                                                        subagent_type,
                                                    )
                                        elif call.get("name") == "write_todos":
                                            # Structured task plan update.
                                            todos = (call.get("args", {}) or {}).get("todos", [])
                                            if todos:
                                                if any(t.get("status") not in (None, "pending") for t in todos):
                                                    agent_managed_todos = True
                                                # Mark the first pending todo as in_progress
                                                # only when the agent does not manage statuses.
                                                if not agent_managed_todos:
                                                    for todo in todos:
                                                        if todo.get("status") == "pending":
                                                            todo["status"] = "in_progress"
                                                            current_todo_idx = todos.index(todo)
                                                            break
                                                latest_todos = todos
                                                yield _sse_event("todo", {
                                                    "todos": todos,
                                                    "agent_id": agent_id,
                                                })
                                        elif call.get("name") == "send_a2ui":
                                            # Fallback projection for models
                                            # that only expose tool calls via
                                            # node updates (no chunks).
                                            _a2args = call.get("args", {}) or {}
                                            try:
                                                _key = json.dumps(
                                                    _a2args, sort_keys=True, ensure_ascii=False
                                                )
                                            except (TypeError, ValueError):
                                                _key = ""
                                            if _key and _key in projected_a2ui_keys:
                                                continue
                                            if _key:
                                                projected_a2ui_keys.add(_key)
                                            surface = _a2ui_surface_from_args(_a2args)
                                            if surface:
                                                a2ui_surfaces.append(surface)
                                                yield _sse_event("a2ui", {
                                                    **surface,
                                                    "agent_id": agent_id,
                                                })
                                    yield _sse_event("subagents", {
                                        "node": node_name,
                                        "tool_calls": calls,
                                        "agent_id": agent_id,
                                    })
                                    # Advance todo status based on tool execution.
                                    # write_todos itself does not advance progress.
                                    if any(c.get("name") != "write_todos" for c in calls):
                                        updated_todos = _advance_todo_status()
                                        if updated_todos:
                                            yield _sse_event("todo", {
                                                "todos": updated_todos,
                                                "agent_id": agent_id,
                                            })

            elif mode == "values":
                # Full state values — detect interrupts.
                if isinstance(data, dict):
                    interrupts = data.get(_INTERRUPT_KEY, [])
                    if interrupts:
                        approval_info = _build_approval_request_info(interrupts)
                        latest_approval = approval_info
                        yield _sse_event("interrupts", {
                            "status": "pending_approval",
                            "approval_request": approval_info,
                            "agent_id": agent_id,
                            "session_id": session_id,
                        })

    except Exception as exc:
        logger.exception("Streaming error for agent %s", agent_id)
        # Keep whatever was produced before the failure so the session
        # history does not silently lose the partial turn.
        _persist_partial()
        yield _sse_event("error", {
            "detail": _friendly_stream_error(exc),
            "agent_id": agent_id,
        })
        return
    finally:
        # Client abort / stop: the generator is closed here (CancelledError
        # is a BaseException, not caught above) — keep the partial turn.
        _persist_partial()
        if app_state is not None:
            await mark_turn_end(app_state, agent_id)
            # Pull agent-created files back into sandbox_storage/ (detached
            # so it never delays the done event or dies with the stream).
            _schedule_pull(app_state, agent_id, workspace)

    # Persist the full assistant turn: reply text, reasoning, tool calls,
    # todo plan, delegations and any approval request.
    _persist_partial()

    # Record token consumption (best-effort; never breaks the chat flow).
    if total_input or total_output:
        from agentcore.runtime.token_router import record_token_usage
        from agentcore.runtime.model_factory import build_model_string

        _model_str = ""
        manager = getattr(app_state, "agent_manager", None) if app_state is not None else None
        if manager is not None:
            try:
                _ws = manager.get_workspace(agent_id)
                if _ws is not None:
                    _cfg = _ws.read_agent_config()
                    _model_str = build_model_string(_cfg.get("model")) or ""
            except Exception:
                pass
        record_token_usage(agent_id, total_input, total_output, _model_str)

    # Emit final done event.
    yield _sse_event("done", {
        "agent_id": agent_id,
        "session_id": session_id,
        "input_tokens": total_input,
        "output_tokens": total_output,
    })

    # Auto-generate session title using AI (best-effort, background task).
    if store is not None and workspace is not None:
        session = store.load_session(session_id)
        if session and not session.get("title"):
            messages = session.get("messages", [])
            if messages:
                try:
                    from agentcore.runtime.model_factory import resolve_model
                    model_cfg = workspace.read_agent_config().get("model") or {}
                    model = resolve_model(model_cfg)
                    if model is not None:
                        asyncio.create_task(
                            _ai_generate_session_title(
                                store, session_id, messages, model
                            )
                        )
                except Exception:
                    logger.debug("Failed to start title generation", exc_info=True)


async def _ai_generate_session_title(
    store: Any,
    session_id: str,
    messages: list[dict[str, Any]],
    model: Any,
) -> None:
    """Generate a concise session title using the agent's LLM.

    Runs as a background task — failures are silently logged.
    """
    try:
        from langchain_core.messages import HumanMessage as _HumanMessage

        # Build a conversation summary from the first few messages.
        summary_parts: list[str] = []
        for msg in messages[:6]:
            role = msg.get("role", "")
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                summary_parts.append(f"\u7528\u6237: {content[:200]}")
            elif role == "assistant":
                summary_parts.append(f"\u52a9\u624b: {content[:200]}")
        if not summary_parts:
            return

        conversation = "\n".join(summary_parts)
        prompt = (
            f"\u6839\u636e\u4ee5\u4e0b\u5bf9\u8bdd\u5185\u5bb9\uff0c\u751f\u6210\u4e00\u4e2a\u7b80\u6d01\u7684\u4f1a\u8bdd\u6807\u9898\u3002"
            f"\u8981\u6c42\uff1a\u4e0d\u8d85\u8fc710\u4e2a\u5b57\uff0c\u4e0d\u8981\u5f15\u53f7\uff0c\u4e0d\u8981\u526f\u6807\u9898\u3002"
            f"\u53ea\u8fd4\u56de\u6807\u9898\u6587\u672c\u3002\n\n\u5bf9\u8bdd\u5185\u5bb9\uff1a\n{conversation}"
        )

        # Resolve to a callable chat model.
        chat_model = model
        if isinstance(model, str):
            from langchain.chat_models import init_chat_model
            chat_model = init_chat_model(model)

        from langchain_core.messages import SystemMessage as _SystemMessage
        response = await chat_model.ainvoke([
            _SystemMessage(content="\u4f60\u662f\u4e00\u4e2a\u4f1a\u8bdd\u6807\u9898\u751f\u6210\u5668\u3002\u53ea\u8fd4\u56de\u6807\u9898\u6587\u672c\uff0c\u4e0d\u8981\u4efb\u4f55\u89e3\u91ca\u3002"),
            _HumanMessage(content=prompt),
        ])

        title = (response.content or "").strip().strip('"\'').strip()
        if not title or len(title) > 50:
            return

        session = store.load_session(session_id)
        if session and not session.get("title"):
            session["title"] = title
            store.save_session(session_id, session)
            logger.info("AI-generated session title: %r", title)
    except Exception:
        logger.debug("AI title generation failed", exc_info=True)

def _serialise_node_output(node_output: Any) -> dict[str, Any]:
    """Best-effort serialisation of a node update for SSE."""
    if isinstance(node_output, dict):
        result: dict[str, Any] = {}
        for k, v in node_output.items():
            if isinstance(v, (str, int, float, bool)):
                result[k] = v
            elif isinstance(v, list):
                result[k] = [_json_safe(item) for item in v]
            elif isinstance(v, dict):
                result[k] = {str(kk): str(vv) for kk, vv in v.items()}
            else:
                result[k] = str(v)
        return result
    return {"value": str(node_output)}


def _json_safe(value: Any) -> Any:
    """Recursively coerce a value into a JSON-serialisable shape."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


@router.post("/stream")
async def stream_chat(request: ChatRequest, req: Request) -> StreamingResponse:
    """Streaming chat — SSE output with 4 projection channels.

    Events:
    - ``messages``: token-by-token LLM output
    - ``subagents``: subagent delegation / tool execution updates
    - ``interrupts``: HITL approval requests
    - ``token_usage``: cumulative token consumption
    - ``done``: stream complete
    - ``error``: an error occurred
    """
    store = _get_store(req)
    # Zombie-resurrection guard — see the ``chat`` endpoint above for
    # the rationale.  ``stream_chat`` is the primary entry point the
    # frontend hits, so a stale tab retaining a deleted ``agent_id``
    # here would both recreate the workspace directory and repopulate
    # ``store.sessions`` with orphan records, immediately undoing the
    # startup purge.
    if request.agent_id not in known_agent_ids(req.app.state):
        raise HTTPException(
            status_code=404,
            detail=f"Agent {request.agent_id!r} not found",
        )
    session_id = _ensure_session(store, request.session_id, request.agent_id)
    _append_message(
        store,
        session_id,
        "user",
        request.message,
        extras=_attachment_extras(request.attachments),
    )

    workspace = None
    manager = getattr(req.app.state, "agent_manager", None)
    if manager is not None:
        try:
            workspace = await manager.get_or_create_workspace(request.agent_id)
        except Exception:
            logger.exception("Failed to load workspace for agent %s", request.agent_id)

    factory = getattr(req.app.state, "factory", None)
    chat_state = get_chat_state(req.app.state)

    # Load agent config early for token recording and sandbox management
    agent_config = {}
    if workspace is not None:
        try:
            agent_config = workspace.read_agent_config()
        except Exception:
            logger.exception("Failed to read agent config for %s", request.agent_id)

    # --- Sandbox session management ---
    sandbox_backend = None
    sandbox_recreated = False  # Track if sandbox was recreated due to death
    sandbox_mgr = getattr(req.app.state, "sandbox_session_manager", None)
    if sandbox_mgr is not None and workspace is not None:
        try:
            backend_cfg = (agent_config.get("settings") or {}).get("backend", {})
            if backend_cfg.get("type") == "sandbox":
                session = await sandbox_mgr.get_or_create(
                    request.agent_id, backend_cfg
                )
                if session is not None:
                    sandbox_backend = session.backend
                    sandbox_recreated = getattr(session, "_sandbox_recreated", False)
                    logger.info(
                        "Sandbox session ready for agent %s (id=%s, status=%s, recreated=%s)",
                        request.agent_id,
                        session.sandbox_id,
                        session.status,
                        sandbox_recreated,
                    )
        except Exception:
            logger.exception(
                "Failed to get/create sandbox session for agent %s",
                request.agent_id,
            )

    # If sandbox was recreated, invalidate graph cache to force
    # creation of a new graph with the new backend
    if sandbox_recreated and chat_state is not None:
        chat_state.graph_cache.pop(request.agent_id, None)
        logger.info(
            "Invalidated graph cache for agent %s (sandbox recreated)",
            request.agent_id,
        )

    # --- Sandbox workspace sync -----------------------------------------
    # Upload local workspace files (skills, memory, kernel) to the sandbox
    # container so the SDK's middleware can discover them through the backend.
    if sandbox_backend is not None and workspace is not None:
        try:
            await _sync_workspace_to_sandbox(workspace, sandbox_backend)
        except Exception:
            logger.exception(
                "Failed to sync workspace to sandbox for agent %s",
                request.agent_id,
            )

    agent_graph = await _resolve_agent_graph(
        request.agent_id,
        workspace,
        factory=factory,
        manager=manager,
        chat_state=chat_state,
        sandbox_backend=sandbox_backend,
    )

    # The outer keep-alive wrapper emits SSE comment lines during idle
    # periods (e.g. waiting for a HITL approval) so the connection is
    # not dropped by browsers or proxies.
    return StreamingResponse(
        keepalive_sse(
            _stream_chat_sse(
                agent_graph, session_id, request.agent_id,
                _with_attachment_note(request.message, request.attachments),
                chat_state, store=store,
                workspace=workspace,
                app_state=req.app.state,
            )
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _session_has_pending_approval(data: dict) -> bool:
    """Return True when the last assistant message carries an un-decided
    approval request (requests expired by the cleanup job don't count)."""
    messages: list[dict] = data.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            if (
                msg.get("approval_request")
                and not msg.get("approval")
                and not msg.get("approval_expired")
            ):
                return True
            # Only the most recent assistant message matters.
            return False
    return False


def _session_has_expired_approval(data: dict) -> bool:
    """Return True when the last assistant message was auto-expired.

    Such sessions were pending approvals raised by background runs that
    nobody answered in time; the Inbox can surface them as expired.
    """
    return has_expired_approval(data)


COLON = None


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(
    req: Request,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """List all sessions, optionally filtered by *agent_id*.

    Each entry carries ``has_pending_approval`` so the frontend can
    highlight sessions that are paused waiting for a HITL decision
    (e.g. background heartbeat / memory consolidation / cron runs).
    """
    store = _get_store(req)
    all_sessions = store.load_sessions()

    # Defensive filter: hide sessions belonging to deleted agents.
    # Startup purge normally removes them, but a browser tab that raced
    # the delete could still leave an orphan record behind — showing it
    # in the sessions list is what made the sessions page appear to
    # have "more agents" than the management page.
    known = known_agent_ids(req.app.state)

    results: list[dict[str, Any]] = []
    for sid, data in all_sessions.items():
        owner = data.get("agent_id") or ""
        if owner and owner not in known:
            continue
        if agent_id is not None and owner != agent_id:
            continue
        results.append({
            "session_id": data.get("session_id", sid),
            "agent_id": data.get("agent_id", ""),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "message_count": len(data.get("messages", [])),
            "has_pending_approval": _session_has_pending_approval(data),
            "has_expired_approval": _session_has_expired_approval(data),
            "title": data.get("title", ""),
        })

    results.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return results


class RenameSessionRequest(BaseModel):
    """Payload for renaming a session."""
    title: str


@router.post("/sessions/{session_id}/rename")
async def rename_session(
    session_id: str,
    payload: RenameSessionRequest,
    req: Request,
) -> dict[str, Any]:
    """Rename a session.

    Updates the session's ``title`` field.  Empty string clears the title
    (reverts to auto-generated display).
    """
    store = _get_store(req)
    session = store.load_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"Session {session_id!r} not found",
        )
    session["title"] = payload.title.strip()
    session["updated_at"] = _now_iso()
    store.save_session(session_id, session)
    logger.info("Session %s renamed to %r", session_id, session["title"])
    return {"session_id": session_id, "title": session["title"]}


# ---------------------------------------------------------------------------
# HITL approval decision endpoint (Tasks 1.1, 1.3, 1.4)
# ---------------------------------------------------------------------------


@router.post("/{agent_id}/sessions/{session_id}/approval")
async def submit_approval(
    agent_id: str,
    session_id: str,
    approval: ApprovalRequest,
    req: Request,
) -> ChatResponse:
    """Submit a HITL approval decision for a paused session.

    Resumes the agent graph with the decision via the LangGraph
    checkpointer.  If the agent completes after resuming, the reply is
    returned directly.  If another interrupt fires, a new
    ``pending_approval`` response is returned.
    """
    store = _get_store(req)

    # Validate session exists.
    session = store.load_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id!r} not found",
        )

    # Resolve the cached agent graph (auto-rebuild after server restart).
    chat_state = get_chat_state(req.app.state)
    agent_graph = await _ensure_agent_graph(req, agent_id, chat_state)

    if chat_state.checkpointer is None:
        raise HTTPException(
            status_code=503,
            detail="Checkpointer not available — cannot resume interrupted session.",
        )

    # Load agent config for token recording
    manager = getattr(req.app.state, "agent_manager", None)
    workspace = None
    if manager is not None:
        try:
            workspace = await manager.get_or_create_workspace(agent_id)
        except Exception:
            logger.exception("Failed to load workspace for agent %s", agent_id)
    agent_config = {}
    if workspace is not None:
        try:
            agent_config = workspace.read_agent_config()
        except Exception:
            logger.exception("Failed to read agent config for %s", agent_id)

    # --- Enforce the interrupt policy (allowed_decisions) -----------
    # The SDK's HumanInTheLoopMiddleware embeds each tool's allowed
    # decisions in the interrupt state.  Decisions outside that set are
    # rejected here — the UI only renders allowed buttons, but the API
    # is the actual enforcement boundary.
    if approval.decision == "respond":
        raise HTTPException(
            status_code=501,
            detail=(
                "The 'respond' decision is not supported yet — use "
                "approve / edit / reject."
            ),
        )
    allowed_decisions = await _get_interrupt_allowed_decisions(
        agent_graph, session_id
    )
    if allowed_decisions and approval.decision not in allowed_decisions:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Decision {approval.decision!r} is not allowed for this "
                f"interrupt (allowed: {sorted(allowed_decisions)})."
            ),
        )

    # --- Build the SDK decision objects for ALL pending tool calls -----
    # When multiple tools are called in a single turn, the SDK creates
    # multiple interrupts. We need to provide a decision for each one.
    all_pending_tools = await _get_all_interrupt_tools(agent_graph, session_id)
    if not all_pending_tools:
        # Fallback: no tool info available, use single decision.
        all_pending_tools = [{"name": "", "args": {}}]

    decisions: list[dict[str, Any]] = []
    for tool in all_pending_tools:
        decision: dict[str, Any]
        if approval.decision == "approve":
            decision = {"type": "approve"}
        elif approval.decision == "edit":
            if not approval.edited_args:
                raise HTTPException(
                    status_code=400,
                    detail="edited_args is required for edit decision",
                )
            # For edit, apply the edited args to the first tool only.
            # Other tools get approved with their original args.
            if len(decisions) == 0:
                decision = {
                    "type": "edit",
                    "edited_action": {
                        "name": tool.get("name", ""),
                        "args": approval.edited_args,
                    },
                }
            else:
                decision = {"type": "approve"}
        elif approval.decision == "reject":
            decision = {
                "type": "reject",
                "message": approval.message or "",
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown decision type: {approval.decision!r}",
            )
        decisions.append(decision)

    # --- Audit the decision (Task 1.4) --------------------------------
    # Audit the first tool for simplicity.
    tool_name_for_audit = all_pending_tools[0].get("name") or "unknown"
    final_args = (
        approval.edited_args
        if approval.decision == "edit" and approval.edited_args
        else all_pending_tools[0].get("args", {})
    )
    from agentcore.runtime.audit import record_approval_decision

    record_approval_decision(
        agent_id=agent_id,
        session_id=session_id,
        decision=approval.decision,
        tool_name=tool_name_for_audit,
        final_args=final_args,
        operator=approval.operator,
    )

    # --- Resume the graph with ALL decisions ---------------------------
    config = {"configurable": {"thread_id": session_id}}
    resume_value = {"decisions": decisions}

    await mark_turn_begin(req.app.state, agent_id)
    try:
        result = await asyncio.wait_for(
            agent_graph.ainvoke(
                Command(resume=resume_value),
                config=config,
            ),
            timeout=180,
        )
    except asyncio.TimeoutError:
        logger.error(
            "Agent %s: approval resume timed out in session %s",
            agent_id,
            session_id,
        )
        raise HTTPException(
            status_code=504,
            detail=(
                "Agent did not respond within 180s after approval. "
                "The session may be busy; please try again later."
            ),
        ) from None
    except Exception as exc:
        logger.exception(
            "Agent %s: failed to resume after approval in session %s",
            agent_id,
            session_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resume agent: {exc}",
        ) from exc
    finally:
        await mark_turn_end(req.app.state, agent_id)

    # Check for further interrupts (another approval round).
    more_interrupts = _extract_interrupts_from_result(result)
    if more_interrupts:
        approval_info = _build_approval_request_info(more_interrupts)
        # Record this decision and surface the next pending request on the
        # same persisted message.
        _mark_last_pending_approval(
            store, session_id, approval.decision,
            tool_name_for_audit or "",
            new_approval_request=approval_info,
        )
        return ChatResponse(
            session_id=session_id,
            agent_id=agent_id,
            content="",
            timestamp=_now_iso(),
            status="pending_approval",
            approval_request=approval_info,
        )

    # Agent completed — extract reply.
    content, tool_calls = _extract_text_from_result(result)
    content = _collapse_blank_runs(content)

    # Record the decision on the message that requested the approval, so
    # session history shows the full approve → resume → reply sequence, and
    # drop the stale pending request (the chain completed).
    _mark_last_pending_approval(
        store, session_id, approval.decision, tool_name_for_audit or "",
        clear_request=True,
    )

    _append_message(store, session_id, "assistant", content, tool_calls or None)

    # Record token consumption (best-effort).
    input_tokens, output_tokens = _extract_usage_from_result(result)
    if input_tokens or output_tokens:
        from agentcore.runtime.token_router import record_token_usage
        from agentcore.runtime.model_factory import build_model_string

        _model_str = build_model_string(agent_config.get("model")) or ""
        record_token_usage(agent_id, input_tokens, output_tokens, _model_str)

    return ChatResponse(
        session_id=session_id,
        agent_id=agent_id,
        content=content,
        timestamp=_now_iso(),
        tool_calls=tool_calls,
    )


@router.post("/{agent_id}/sessions/{session_id}/approval/stream")
async def submit_approval_stream(
    agent_id: str,
    session_id: str,
    approval: ApprovalRequest,
    req: Request,
) -> StreamingResponse:
    """Submit a HITL approval and stream the agent's response via SSE.

    This is the streaming version of submit_approval — it resumes the
    agent graph and streams the output using the same SSE format as
    the main chat stream endpoint.
    """
    store = _get_store(req)

    # Validate session exists.
    session = store.load_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id!r} not found",
        )

    # Resolve the cached agent graph (auto-rebuild after server restart).
    chat_state = get_chat_state(req.app.state)
    agent_graph = await _ensure_agent_graph(req, agent_id, chat_state)

    if chat_state.checkpointer is None:
        raise HTTPException(
            status_code=503,
            detail="Checkpointer not available — cannot resume interrupted session.",
        )

    # --- Enforce the interrupt policy --------------------------------
    if approval.decision == "respond":
        raise HTTPException(
            status_code=501,
            detail="The 'respond' decision is not supported yet.",
        )
    allowed_decisions = await _get_interrupt_allowed_decisions(agent_graph, session_id)
    if allowed_decisions and approval.decision not in allowed_decisions:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Decision {approval.decision!r} is not allowed (allowed: {sorted(allowed_decisions)})."
            ),
        )

    # --- Build the SDK decision objects for ALL pending tool calls -----
    all_pending_tools = await _get_all_interrupt_tools(agent_graph, session_id)
    if not all_pending_tools:
        all_pending_tools = [{"name": "", "args": {}}]

    decisions: list[dict[str, Any]] = []
    for tool in all_pending_tools:
        decision: dict[str, Any]
        if approval.decision == "approve":
            decision = {"type": "approve"}
        elif approval.decision == "edit":
            if not approval.edited_args:
                raise HTTPException(
                    status_code=400,
                    detail="edited_args is required for edit decision",
                )
            if len(decisions) == 0:
                decision = {
                    "type": "edit",
                    "edited_action": {
                        "name": tool.get("name", ""),
                        "args": approval.edited_args,
                    },
                }
            else:
                decision = {"type": "approve"}
        elif approval.decision == "reject":
            decision = {
                "type": "reject",
                "message": approval.message or "",
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown decision type: {approval.decision!r}",
            )
        decisions.append(decision)

    # --- Audit the decision ------------------------------------------
    tool_name_for_audit = all_pending_tools[0].get("name") or "unknown"
    final_args = (
        approval.edited_args
        if approval.decision == "edit" and approval.edited_args
        else all_pending_tools[0].get("args", {})
    )
    from agentcore.runtime.audit import record_approval_decision
    record_approval_decision(
        agent_id=agent_id,
        session_id=session_id,
        decision=approval.decision,
        tool_name=tool_name_for_audit,
        final_args=final_args,
        operator=approval.operator,
    )

    # --- Stream the resumed graph ------------------------------------
    config = {"configurable": {"thread_id": session_id}}
    resume_value = {"decisions": decisions}

    return StreamingResponse(
        keepalive_sse(
            _stream_approval_sse(
                agent_graph=agent_graph,
                config=config,
                resume_value=resume_value,
                agent_id=agent_id,
                session_id=session_id,
                store=store,
                decision=approval.decision,
                tool_name_probe=tool_name_for_audit,
                app_state=req.app.state,
            )
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _stream_approval_sse(
    agent_graph: Any,
    config: dict[str, Any],
    resume_value: dict[str, Any],
    agent_id: str,
    session_id: str,
    store: Any,
    decision: str,
    tool_name_probe: str,
    app_state: Any = None,
) -> AsyncIterator[str]:
    """Stream the agent's response after approval via SSE."""
    # Bookkeeping: the agent is busy for the lifetime of this stream.
    if app_state is not None:
        await mark_turn_begin(app_state, agent_id)

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    latest_todos: list[dict[str, Any]] = []
    # A2UI surfaces rendered after the resume (same projection as the
    # main stream).
    a2ui_surfaces: list[dict[str, Any]] = []
    projected_a2ui_keys: set[str] = set()
    total_input = 0
    total_output = 0
    current_todo_idx: int = -1
    # Same heuristic as the main stream: once the agent manages todo
    # statuses itself, disable the auto-advance.
    agent_managed_todos = False

    # Streaming tool-call accumulator (same pattern as main stream).
    pending_tool_chunks: dict[int, dict[str, Any]] = {}
    emitted_tool_call_keys: set[str] = set()

    # Subagent activity collector (same wiring as the main stream): the
    # resumed graph may delegate via ``task`` right after the approval,
    # and delegation runs are invisible to the astream modes.
    collector = _SubagentActivityCollector()
    config = {**config, "callbacks": [collector]}

    def _process_tool_call_chunks_approval(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge tool-call chunk deltas; return calls that became complete."""
        nonlocal latest_todos, current_todo_idx, agent_managed_todos
        completed: list[dict[str, Any]] = []
        for tc in chunks:
            if not isinstance(tc, dict):
                continue
            idx = tc.get("index") or 0
            acc = pending_tool_chunks.setdefault(idx, {"name": "", "args": "", "id": None})
            if tc.get("name"):
                acc["name"] = tc["name"]
            if tc.get("id"):
                acc["id"] = tc["id"]
            acc["args"] += tc.get("args") or ""
            key = acc["id"] or f"{acc['name']}@{idx}"
            if key in emitted_tool_call_keys:
                continue
            try:
                parsed_args = json.loads(acc["args"]) if acc["args"] else None
            except (json.JSONDecodeError, ValueError):
                continue
            if parsed_args is None or not acc["name"]:
                continue
            emitted_tool_call_keys.add(key)
            completed.append({"name": acc["name"], "args": parsed_args})
            if acc["name"] == "write_todos":
                todos = parsed_args.get("todos", []) if isinstance(parsed_args, dict) else []
                if todos:
                    if any(t.get("status") not in (None, "pending") for t in todos):
                        agent_managed_todos = True
                    if not agent_managed_todos:
                        for todo in todos:
                            if todo.get("status") == "pending":
                                todo["status"] = "in_progress"
                                current_todo_idx = todos.index(todo)
                                break
                    latest_todos = todos
        return completed

    def _advance_todo_status_approval() -> list[dict[str, Any]] | None:
        """Advance todo status based on tool execution (approval stream)."""
        nonlocal latest_todos, current_todo_idx
        if agent_managed_todos or not latest_todos:
            return None
        if 0 <= current_todo_idx < len(latest_todos):
            latest_todos[current_todo_idx]["status"] = "completed"
        next_idx = -1
        for i, todo in enumerate(latest_todos):
            if todo.get("status") == "pending":
                next_idx = i
                break
        if next_idx >= 0:
            latest_todos[next_idx]["status"] = "in_progress"
            current_todo_idx = next_idx
        else:
            current_todo_idx = -1
        return latest_todos

    def _persist_partial() -> None:
        """Persist whatever was produced so far (best-effort)."""
        text = "".join(text_parts).strip()
        reasoning = "".join(reasoning_parts).strip()
        if not (text or reasoning or tool_calls or latest_todos or a2ui_surfaces):
            return
        try:
            _append_assistant_turn(
                store, session_id, text_parts, reasoning_parts,
                tool_calls, latest_todos or None, [], None,
                token_usage={"input_tokens": total_input, "output_tokens": total_output},
                a2ui_surfaces=a2ui_surfaces or None,
            )
        except Exception:
            logger.exception(
                "Failed to persist assistant message after approval for session %s",
                session_id,
            )

    try:
        async for item in _merge_stream_sources(
            agent_graph.astream(
                Command(resume=resume_value),
                config=config,
                stream_mode=["updates", "messages", "values"],
            ),
            collector.queue,
        ):
            if item[0] == "activity":
                yield _sse_event("subagent_activity", {
                    **item[1],
                    "agent_id": agent_id,
                })
                continue
            chunk = item[1]
            if not isinstance(chunk, (list, tuple)) or len(chunk) < 2:
                continue

            mode, data = chunk[0], chunk[1]

            if mode == "messages":
                if isinstance(data, (list, tuple)) and len(data) >= 1:
                    msg_chunk = data[0]

                    # Only stream content from AI messages — skip ToolMessage
                    # results that would flood the chat with raw tool output.
                    msg_type = getattr(msg_chunk, "type", None)
                    if msg_type is None and isinstance(msg_chunk, dict):
                        msg_type = msg_chunk.get("type")
                    is_ai_content = msg_type not in ("tool", "human", "chat")

                    content = ""
                    if hasattr(msg_chunk, "content"):
                        content = msg_chunk.content or ""
                    elif isinstance(msg_chunk, dict):
                        content = msg_chunk.get("content", "")
                    if content and is_ai_content:
                        text_parts.append(content)
                        yield _sse_event("messages", {
                            "content": content,
                            "agent_id": agent_id,
                        })

                    reasoning = _extract_chunk_reasoning(msg_chunk)
                    if reasoning:
                        reasoning_parts.append(reasoning)
                        yield _sse_event("messages", {
                            "reasoning": reasoning,
                            "agent_id": agent_id,
                        })

                    # Early tool_calls detection — merge fragmented chunks
                    # and emit only complete calls (same as main stream).
                    raw_chunks = getattr(msg_chunk, "tool_call_chunks", None)
                    if raw_chunks is None and isinstance(msg_chunk, dict):
                        raw_chunks = msg_chunk.get("tool_call_chunks")
                    if raw_chunks:
                        completed_calls = _process_tool_call_chunks_approval(list(raw_chunks))
                        card_calls = [
                            c for c in completed_calls
                            if c["name"] not in ("write_todos", "send_a2ui")
                        ]
                        if card_calls:
                            yield _sse_event("tool_calls", {
                                "tool_calls": card_calls,
                                "agent_id": agent_id,
                            })
                        for call in completed_calls:
                            if call["name"] == "send_a2ui":
                                surface = _a2ui_surface_from_args(call["args"])
                                if surface:
                                    try:
                                        _key = json.dumps(
                                            call["args"], sort_keys=True, ensure_ascii=False
                                        )
                                    except (TypeError, ValueError):
                                        _key = ""
                                    if _key and _key in projected_a2ui_keys:
                                        continue
                                    if _key:
                                        projected_a2ui_keys.add(_key)
                                    a2ui_surfaces.append(surface)
                                    yield _sse_event("a2ui", {
                                        **surface,
                                        "agent_id": agent_id,
                                    })
                        for call in completed_calls:
                            if call["name"] == "write_todos":
                                args = call["args"]
                                todos_payload = (
                                    args.get("todos", [])
                                    if isinstance(args, dict) else []
                                )
                                if todos_payload:
                                    yield _sse_event("todo", {
                                        "todos": todos_payload,
                                        "agent_id": agent_id,
                                    })

                    usage = _extract_chunk_usage(
                        msg_chunk, data[1] if len(data) > 1 else None
                    )
                    if usage:
                        total_input += usage["input_tokens"]
                        total_output += usage["output_tokens"]
                        yield _sse_event("token_usage", {
                            "input_tokens": total_input,
                            "output_tokens": total_output,
                            "agent_id": agent_id,
                        })

            elif mode == "updates":
                if isinstance(data, dict):
                    for node_name, node_output in data.items():
                        if node_name == "task" or "subagent" in node_name.lower():
                            yield _sse_event("subagents", {
                                "node": node_name,
                                "update": _serialise_node_output(node_output),
                                "agent_id": agent_id,
                            })
                        if isinstance(node_output, dict):
                            msgs = node_output.get("messages", [])
                            for m in msgs:
                                tc = getattr(m, "tool_calls", None) or (
                                    m.get("tool_calls") if isinstance(m, dict) else None
                                )
                                if tc:
                                    calls = [
                                        {
                                            "name": c.get("name", "") if isinstance(c, dict) else getattr(c, "name", ""),
                                            "args": c.get("args", {}) if isinstance(c, dict) else getattr(c, "args", {}),
                                        }
                                        for c in tc
                                    ]
                                    tool_calls.extend(calls)
                                    for call in calls:
                                        if call.get("name") == "send_a2ui":
                                            # Fallback projection (same as
                                            # the main stream's updates mode).
                                            _a2args = call.get("args", {}) or {}
                                            try:
                                                _key = json.dumps(
                                                    _a2args, sort_keys=True, ensure_ascii=False
                                                )
                                            except (TypeError, ValueError):
                                                _key = ""
                                            if not (_key and _key in projected_a2ui_keys):
                                                if _key:
                                                    projected_a2ui_keys.add(_key)
                                                surface = _a2ui_surface_from_args(_a2args)
                                                if surface:
                                                    a2ui_surfaces.append(surface)
                                                    yield _sse_event("a2ui", {
                                                        **surface,
                                                        "agent_id": agent_id,
                                                    })
                                        elif call.get("name") == "write_todos":
                                            todos = (call.get("args", {}) or {}).get("todos", [])
                                            if todos:
                                                if any(t.get("status") not in (None, "pending") for t in todos):
                                                    agent_managed_todos = True
                                                # Mark the first pending todo as in_progress
                                                # only when the agent does not manage statuses.
                                                if not agent_managed_todos:
                                                    for todo in todos:
                                                        if todo.get("status") == "pending":
                                                            todo["status"] = "in_progress"
                                                            current_todo_idx = todos.index(todo)
                                                            break
                                                latest_todos = todos
                                                yield _sse_event("todo", {
                                                    "todos": todos,
                                                    "agent_id": agent_id,
                                                })
                                    yield _sse_event("subagents", {
                                        "node": node_name,
                                        "tool_calls": calls,
                                        "agent_id": agent_id,
                                    })
                                    # Advance todo status based on tool execution.
                                    # write_todos itself does not advance progress.
                                    if any(c.get("name") != "write_todos" for c in calls):
                                        updated_todos = _advance_todo_status_approval()
                                        if updated_todos:
                                            yield _sse_event("todo", {
                                                "todos": updated_todos,
                                                "agent_id": agent_id,
                                            })

            elif mode == "values":
                if isinstance(data, dict):
                    interrupts = data.get(_INTERRUPT_KEY, [])
                    if interrupts:
                        approval_info = _build_approval_request_info(interrupts)
                        yield _sse_event("interrupts", {
                            "status": "pending_approval",
                            "approval_request": approval_info,
                            "agent_id": agent_id,
                            "session_id": session_id,
                        })

    except Exception as exc:
        logger.exception("Streaming error after approval for agent %s", agent_id)
        _persist_partial()
        yield _sse_event("error", {
            "detail": _friendly_stream_error(exc),
            "agent_id": agent_id,
        })
        return
    finally:
        _persist_partial()
        if app_state is not None:
            await mark_turn_end(app_state, agent_id)
            # Same post-turn pull as the main chat stream (workspace is
            # resolved from the agent manager inside the helper).
            _schedule_pull(app_state, agent_id, None)

    # Record the decision on the message.
    _mark_last_pending_approval(
        store, session_id, decision, tool_name_probe or "",
        clear_request=True,
    )

    # Record token consumption.
    if total_input or total_output:
        from agentcore.runtime.token_router import record_token_usage
        from agentcore.runtime.model_factory import build_model_string

        _model_str = ""
        manager = getattr(app_state, "agent_manager", None) if app_state is not None else None
        if manager is not None:
            try:
                _ws = manager.get_workspace(agent_id)
                if _ws is not None:
                    _cfg = _ws.read_agent_config()
                    _model_str = build_model_string(_cfg.get("model")) or ""
            except Exception:
                pass
        record_token_usage(agent_id, total_input, total_output, _model_str)

    yield _sse_event("done", {
        "agent_id": agent_id,
        "session_id": session_id,
        "input_tokens": total_input,
        "output_tokens": total_output,
    })


@router.get("/{agent_id}/sessions/{session_id}/approval/status")
async def get_approval_status(
    agent_id: str,
    session_id: str,
    req: Request,
) -> dict[str, Any]:
    """Check whether a session is still waiting for approval.

    Returns the current interrupt state (if any) so the frontend can
    poll without sending a full chat message.
    """
    chat_state = get_chat_state(req.app.state)
    agent_graph = await _ensure_agent_graph(req, agent_id, chat_state)

    config = {"configurable": {"thread_id": session_id}}
    try:
        snapshot = await agent_graph.aget_state(config)
    except Exception as exc:
        logger.exception("Failed to get state for session %s", session_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get session state: {exc}",
        ) from exc

    interrupts = list(snapshot.interrupts) if snapshot.interrupts else []
    if not interrupts:
        return {"status": "complete", "approval_request": None}

    approval_info = _build_approval_request_info(interrupts)
    return {"status": "pending_approval", "approval_request": approval_info}


async def _aget_interrupt_payloads(
    agent_graph: Any, session_id: str
) -> list[Any]:
    """Async-safe extraction of current interrupt payload values.

    The production checkpointer is an ``AsyncSqliteSaver`` whose *sync*
    ``get_state`` raises ``NotImplementedError`` — everything must go
    through ``aget_state``. Values may be dicts or pydantic ``HITLRequest``
    objects depending on the serialisation path; normalise to dicts.
    """
    payloads: list[Any] = []
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await agent_graph.aget_state(config)
    for interrupt_obj in (snapshot.interrupts or []):
        value = getattr(interrupt_obj, "value", None)
        if isinstance(value, dict):
            payloads.append(value)
        elif value is not None and hasattr(value, "model_dump"):
            try:
                payloads.append(value.model_dump())
            except Exception:
                logger.debug("Could not serialise interrupt payload %r", value)
    return payloads


async def _get_interrupt_allowed_decisions(
    agent_graph: Any, session_id: str
) -> set[str] | None:
    """Union of allowed decisions across the session's current interrupts.

    Returns ``None`` when no policy information is visible (callers then
    skip the enforcement check, preserving legacy behaviour).
    """
    try:
        allowed: set[str] = set()
        seen_policy = False
        for value in await _aget_interrupt_payloads(agent_graph, session_id):
            for rc in value.get("review_configs", []):
                decisions = (
                    rc.get("allowed_decisions") if isinstance(rc, dict) else None
                )
                if decisions:
                    seen_policy = True
                    allowed.update(decisions)
        return allowed if seen_policy else None
    except Exception:
        logger.warning("Could not extract allowed decisions from interrupt state", exc_info=True)
        return None


async def _get_first_interrupt_tool_name(
    agent_graph: Any, session_id: str
) -> str | None:
    """Best-effort extraction of the first interrupted tool name."""
    try:
        for value in await _aget_interrupt_payloads(agent_graph, session_id):
            actions = value.get("action_requests", [])
            if actions:
                first = actions[0]
                return first.get("name") if isinstance(first, dict) else getattr(first, "name", None)
    except Exception:
        logger.debug("Could not extract tool name from interrupt state")
    return None


async def _get_all_interrupt_tools(
    agent_graph: Any, session_id: str
) -> list[dict[str, Any]]:
    """Extract all interrupted tool names and args.

    Returns a list of dicts with 'name' and 'args' keys for each
    pending tool call — the count MUST match the number of decisions
    sent on resume, otherwise the SDK raises a ValueError.
    """
    tools: list[dict[str, Any]] = []
    try:
        for value in await _aget_interrupt_payloads(agent_graph, session_id):
            for action in value.get("action_requests", []):
                if isinstance(action, dict):
                    tools.append({
                        "name": action.get("name", ""),
                        "args": action.get("args", {}),
                    })
                else:
                    tools.append({
                        "name": getattr(action, "name", ""),
                        "args": getattr(action, "args", {}) or {},
                    })
    except Exception:
        logger.warning("Could not extract tools from interrupt state", exc_info=True)
    return tools


def _get_first_interrupt_args(
    agent_graph: Any, session_id: str
) -> dict[str, Any]:
    """Best-effort extraction of the first interrupted tool args."""
    try:
        config = {"configurable": {"thread_id": session_id}}
        snapshot = agent_graph.get_state(config)
        for interrupt_obj in (snapshot.interrupts or []):
            value = getattr(interrupt_obj, "value", None)
            if isinstance(value, dict):
                actions = value.get("action_requests", [])
                if actions:
                    return actions[0].get("args", {})
    except Exception:
        logger.debug("Could not extract tool args from interrupt state")
    return {}


@router.get("/history")
async def chat_history(
    req: Request,
    session_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Retrieve message history for a session."""
    store = _get_store(req)
    session = store.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")

    messages: list[dict] = session.get("messages", [])
    recent = messages[-limit:] if len(messages) > limit else messages
    # Assistant text is stored verbatim as it was streamed, so it is returned
    # untouched — rewriting it here (e.g. collapsing single newlines into
    # spaces) would make history render differently from the live answer.
    return recent


async def _clear_session_runtime(chat_state: Any, session_id: str) -> None:
    """Drop a session's checkpoint state and stop tracking it for purges.

    Shared by single and batch deletion so a future session with the same
    id starts clean.
    """
    if chat_state.checkpointer is not None:
        try:
            await chat_state.checkpointer.adelete_thread(session_id)
        except Exception:
            logger.exception(
                "Failed to delete checkpoint state for session %s", session_id
            )
    for threads in chat_state.threads.values():
        threads.discard(session_id)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, req: Request) -> dict[str, str]:
    """Delete a session and its message history."""
    store = _get_store(req)
    session = store.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")

    store.delete_session(session_id)
    await _clear_session_runtime(get_chat_state(req.app.state), session_id)

    return {"result": "ok", "session_id": session_id}


@router.post("/sessions/batch-delete")
async def batch_delete_sessions(
    body: BatchDeleteSessionsRequest,
    req: Request,
) -> dict[str, Any]:
    """Delete multiple sessions and their message histories at once.

    Unknown session ids are skipped and reported in ``not_found`` rather
    than failing the whole request; deduplicated ids are processed once.
    """
    store = _get_store(req)
    chat_state = get_chat_state(req.app.state)
    ids = list(dict.fromkeys(body.session_ids))

    deleted: list[str] = []
    not_found: list[str] = []
    existing = store.load_sessions()
    for session_id in ids:
        if session_id in existing:
            deleted.append(session_id)
        else:
            not_found.append(session_id)

    if deleted:
        store.delete_sessions(deleted)
        for session_id in deleted:
            await _clear_session_runtime(chat_state, session_id)

    return {"deleted": deleted, "not_found": not_found}


# ---------------------------------------------------------------------------
# Session trace (LangSmith-style per-step timeline)
#
# Every LangGraph super-step writes one checkpoint row plus per-channel
# writes rows into ``checkpoints.db`` (thread_id == session_id).  The
# ``metadata`` JSON carries the step sequence number and the ``writes``
# rows hold the serialized message deltas for that step, so a session's
# full execution trace (model turns, tool calls, tool results) can be
# reconstructed read-only without any external tracing service.
# ---------------------------------------------------------------------------

# Shared serializer for checkpoint/writes blobs (msgpack in this SDK).
_TRACE_SERDE = JsonPlusSerializer()


def _trace_message_to_event(msg: Any) -> dict[str, Any]:
    """Convert one LangChain message (object or dict) to a trace event."""
    if isinstance(msg, dict):
        kind = str(msg.get("type") or msg.get("role") or "unknown")
        name = msg.get("name") or None
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls") or []
        tool_call_id = msg.get("tool_call_id")
        usage = msg.get("usage_metadata")
    else:
        kind = str(getattr(msg, "type", "unknown"))
        name = getattr(msg, "name", None) or None
        content = getattr(msg, "content", "")
        tool_calls = getattr(msg, "tool_calls", None) or []
        tool_call_id = getattr(msg, "tool_call_id", None)
        usage = getattr(msg, "usage_metadata", None)

    # Content may be a list of typed blocks (text/image) in newer models.
    if isinstance(content, list):
        text_blocks = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        content = "\n".join(p for p in text_blocks if p) if text_blocks else ""

    event: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "content": str(content) if content else "",
    }
    parsed_calls: list[dict[str, Any]] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        args = tc.get("args", {})
        parsed_calls.append({
            "id": tc.get("id", ""),
            "name": tc.get("name", ""),
            "args": args if isinstance(args, dict) else {},
        })
    if parsed_calls:
        event["tool_calls"] = parsed_calls
    if tool_call_id:
        event["tool_call_id"] = str(tool_call_id)
    if isinstance(usage, dict) and usage:
        event["usage"] = {
            k: int(v) for k, v in usage.items()
            if k in ("input_tokens", "output_tokens", "total_tokens")
            and isinstance(v, (int, float))
        }
    return event


async def _read_session_trace(session_id: str) -> list[dict[str, Any]]:
    """Rebuild a session's step timeline from the checkpoints database.

    Returns a list of steps ordered by execution sequence; each step
    carries its ``metadata.step`` number, ISO timestamp, source and the
    message events written during that step (model turns, tool calls,
    tool results).  Reads are read-only — nothing is mutated.

    A missing checkpoint file (fresh data dir) yields an empty timeline.
    """
    checkpoint_db = paths.get_checkpoints_path()
    if not checkpoint_db.is_file():
        return []

    try:
        conn = await aiosqlite.connect(str(checkpoint_db))
    except Exception:
        logger.exception("Failed to open checkpoints db %s", checkpoint_db)
        return []

    try:
        # checkpoint_id -> (step, source, ts)
        cid_meta: dict[str, tuple[int, str, str | None]] = {}
        rows = await conn.execute(
            "SELECT checkpoint_id, type, checkpoint, metadata "
            "FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ''",
            (session_id,),
        )
        async for cid, ctype, blob, meta_blob in rows:
            step: int | None = None
            source = "loop"
            ts: str | None = None
            if isinstance(meta_blob, (bytes, bytearray)):
                meta_blob = meta_blob.decode("utf-8", errors="replace")
            if isinstance(meta_blob, str) and meta_blob:
                try:
                    meta = json.loads(meta_blob)
                    step = meta.get("step")
                    source = meta.get("source", "loop")
                except Exception:
                    pass
            if step is None:
                continue
            try:
                cp = _TRACE_SERDE.loads_typed((str(ctype), blob))
                if isinstance(cp, dict):
                    ts = str(cp.get("ts")) if cp.get("ts") else None
            except Exception:
                pass
            cid_meta[cid] = (int(step), str(source), ts)

        # Message-channel writes in insertion order.  rowid mirrors the
        # order LangGraph appended writes, so it is the safest sort key.
        rows = await conn.execute(
            "SELECT checkpoint_id, rowid, channel, type, value "
            "FROM writes WHERE thread_id = ? AND checkpoint_ns = '' "
            "AND channel = 'messages' ORDER BY rowid",
            (session_id,),
        )
        steps: dict[int, dict[str, Any]] = {}
        async for cid, _rowid, _channel, wtype, value in rows:
            meta = cid_meta.get(cid)
            if meta is None:
                continue
            step, source, ts = meta
            evs = steps.setdefault(
                step,
                {"step": step, "source": source, "ts": ts, "events": []},
            )
            try:
                decoded = _TRACE_SERDE.loads_typed((str(wtype), value))
            except Exception:
                logger.debug("trace: undecodable write for %s", cid)
                continue
            messages = decoded if isinstance(decoded, list) else [decoded]
            for m in messages:
                if m is None:
                    continue
                evs["events"].append(_trace_message_to_event(m))
    except Exception:
        logger.exception("Failed to read session trace for %s", session_id)
        return []
    finally:
        await conn.close()

    return [steps[s] for s in sorted(steps)]


@router.get("/sessions/{session_id}/trace")
async def get_session_trace(
    req: Request,
    session_id: str,
) -> dict[str, Any]:
    """Return a LangSmith-style step timeline for a session (read-only).

    The timeline is rebuilt live from the local checkpoints database —
    no external tracing service involved.  Steps come back ordered by
    execution sequence with per-step events (model turns, tool calls and
    their results, usage metadata).
    """
    store = _get_store(req)
    if store.load_session(session_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Session {session_id!r} not found"
        )
    steps = await _read_session_trace(session_id)
    return {"session_id": session_id, "steps": steps}
