"""FastAPI application for AgentCore control plane."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentcore import auth_router
from agentcore.auth import AuthMiddleware
from agentcore.menu import default_navigation_domains
from agentcore.repository import ControlPlaneStore
from agentcore.runtime import paths
from agentcore.runtime.agent_factory import AgentFactory
from agentcore.runtime.agent_runtime import AgentRuntime
from agentcore.runtime.multi_agent_manager import (
    MultiAgentManager,
    _rmtree_with_retry,
    migrate_workspace_layout,
)
from agentcore.runtime import agent_router, chat_router, files_router
from agentcore.runtime import cron_router
from agentcore.runtime import debug_router
from agentcore.runtime import heartbeat_router
from agentcore.runtime import memory_router
from agentcore.runtime import envs_router, models_router, stats_router
from agentcore.runtime import mcp_router, skills_router
from agentcore.runtime import security_router
from agentcore.runtime import token_router, tools_router
from agentcore.runtime.cron_manager import CronManager
from agentcore.service import AgentService

# ---------------------------------------------------------------------------
# Module-level references — populated during lifespan startup.
# Route functions resolve these at call-time (Python global scoping), so
# assigning during lifespan is safe.  The defaults ensure the app remains
# usable even without lifespan (e.g. synchronous test clients).
# ---------------------------------------------------------------------------

store: ControlPlaneStore = ControlPlaneStore()
factory: AgentFactory = AgentFactory()
agent_manager: MultiAgentManager = MultiAgentManager(factory=factory, repository=store)
service: AgentService = AgentService(store, agent_manager=agent_manager)
runtime: AgentRuntime | None = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sub-path deployment (K8s multi-instance)
#
# When ``AGENTCORE_BASE_PATH`` is set (e.g. ``/instance01``), this app is
# served behind that URL prefix.  ``BasePathMiddleware`` strips the prefix
# before routing, so none of the registered routes need to know about it.
# Requests that arrive without the prefix (K8s liveness probes hitting the
# pod directly, plain docker runs) pass through untouched — an unset/empty
# env var keeps the legacy behaviour exactly.
# ---------------------------------------------------------------------------

_BASE_PATH_PLACEHOLDER = "__AGENTCORE_BASE_PATH__"


def get_base_path() -> str:
    """Normalised ``AGENTCORE_BASE_PATH``: leading ``/``, no trailing ``/``.

    Returns ``""`` when unset — root-path deployment.
    """
    raw = os.environ.get("AGENTCORE_BASE_PATH", "").strip(" \t/")
    return f"/{raw}" if raw else ""


def _inject_base_path(content: str) -> str:
    """Replace build-time base-path placeholders with the runtime value.

    The slashed form ``/__AGENTCORE_BASE_PATH__`` covers asset URLs (Vite
    ``base`` prefix); the bare form covers the inline injection script.
    Order matters: slash form first, otherwise asset refs end up as
    ``//instance01/...``.
    """
    if _BASE_PATH_PLACEHOLDER not in content:
        return content
    base = get_base_path()
    content = content.replace("/" + _BASE_PATH_PLACEHOLDER, base)
    return content.replace(_BASE_PATH_PLACEHOLDER, base)


class BasePathMiddleware:
    """Strip the configured URL prefix from incoming request paths.

    Pure ASGI middleware (no response buffering) so SSE streaming responses
    pass through untouched.
    """

    def __init__(self, app):
        self.app = app
        self.base_path = get_base_path()

    async def __call__(self, scope, receive, send):
        if self.base_path and scope.get("type") in {"http", "websocket"}:
            path = scope.get("path", "")
            if path == self.base_path:
                scope = {**scope, "path": "/", "root_path": self.base_path}
            elif path.startswith(self.base_path + "/"):
                scope = {
                    **scope,
                    "path": path[len(self.base_path):],
                    "root_path": self.base_path,
                }
        await self.app(scope, receive, send)


async def _ensure_default_agent(
    manager: MultiAgentManager, store: ControlPlaneStore
) -> None:
    """Ensure at least one agent exists — a seeded ``default`` agent.

    Idempotent: when any agent is already known (persisted or loaded) this
    is a no-op.  The default agent's workspace is seeded on first creation
    with the kernel files plus ``bootstrap.md`` first-run guidance; failures
    are logged but never block startup.
    """
    if store.agent_states:
        return

    agent_id = "default"
    try:
        await manager.get_or_create_workspace(agent_id)
        store.save_agent_state(
            agent_id,
            {
                "agent_id": agent_id,
                "state": "idle",
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "last_active_at": None,
                "error": None,
            },
        )
        logger.info("Created Default Agent %r with bootstrap.md", agent_id)
    except Exception:
        logger.exception(
            "Failed to ensure Default Agent — startup continues"
        )


def _purge_orphan_workspace_dirs(store: ControlPlaneStore) -> None:
    """Remove on-disk / in-store artefacts with no persisted agent record.

    Prior to the full-cleanup delete-agent fix, deleting an agent could
    leave behind orphan artefacts in:

    * ``.agentcore/workspace/agent/{id}/``  (workspace files)
    * ``.agentcore/data/agents/{id}/``      (sessions / runtime data)
    * ``.agentcore/data/sessions/*``        (chat session records)
    * ``.agentcore/data/delegations.json``  (cross-agent delegation log)

    Any of these would make a "deleted" agent reappear in the UI (the
    sessions page derives its agent filter from ``session.agent_id``, so
    orphan sessions keep showing a phantom agent).  This one-time purge
    runs at startup and drops every artefact whose owner id is not in
    ``store.agent_states`` — subsequent deletions clean up properly on
    their own.
    """
    persisted_ids = set(store.agent_states.keys())

    # 1) Workspace directories (.agentcore/workspace/agent/*)
    ws_root = paths.get_workspace_root()
    if ws_root.exists():
        for entry in ws_root.iterdir():
            if entry.is_dir() and entry.name not in persisted_ids:
                _rmtree_with_retry(entry)
                if not entry.exists():
                    logger.info(
                        "Purged orphan workspace directory: %s", entry.name
                    )
                else:
                    logger.warning(
                        "Failed to purge orphan workspace directory: %s",
                        entry,
                    )

    # 2) Agent data directories (.agentcore/data/agents/*)
    data_root = paths.get_data_dir() / "data" / "agents"
    if data_root.exists():
        for entry in data_root.iterdir():
            if entry.is_dir() and entry.name not in persisted_ids:
                _rmtree_with_retry(entry)
                if not entry.exists():
                    logger.info(
                        "Purged orphan agent data directory: %s", entry.name
                    )
                else:
                    logger.warning(
                        "Failed to purge orphan agent data directory: %s",
                        entry,
                    )

    # 3) Orphan chat sessions — sessions whose ``agent_id`` no longer
    #    resolves to a persisted agent.  Their presence makes the sessions
    #    page show phantom agents in the filter dropdown.
    orphan_sids = [
        sid
        for sid, data in store.sessions.items()
        if data.get("agent_id") and data.get("agent_id") not in persisted_ids
    ]
    if orphan_sids:
        store.delete_sessions(orphan_sids)
        logger.info(
            "Purged %d orphan chat session(s) belonging to deleted agents",
            len(orphan_sids),
        )

    # 4) Orphan delegation records
    orphan_delegations = [
        aid for aid in list(store.delegations.keys())
        if aid not in persisted_ids
    ]
    if orphan_delegations:
        for aid in orphan_delegations:
            store.delegations.pop(aid, None)
        store._save_delegations()
        logger.info(
            "Purged %d orphan delegation record set(s)",
            len(orphan_delegations),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: initialise shared resources on startup."""
    global store, service, runtime, factory, agent_manager

    # Mirror all Python logging into logs/backend.log so the console
    # Debug page can inspect it (idempotent; never blocks startup).
    debug_router.setup_file_logging()

    # --- Startup -----------------------------------------------------------
    # Ensure the data root exists, then migrate the legacy
    # ``.agentcore/workspaces/`` layout to the new directory structure
    # (idempotent; failures never block startup).
    paths.get_data_dir().mkdir(parents=True, exist_ok=True)
    migrate_workspace_layout()

    # Restore persisted environment variables (``.agentcore/envs.json``)
    # into ``os.environ`` before any model is built — LLM API keys saved
    # through ``PUT /api/envs`` must survive restarts.  Never overrides
    # variables already set at process level.
    envs_router.load_envs()

    store = ControlPlaneStore()
    factory = AgentFactory()
    runtime = AgentRuntime(factory=factory, repository=store)
    agent_manager = MultiAgentManager(factory=factory, repository=store)
    service = AgentService(store, runtime=runtime, agent_manager=agent_manager)

    # Purge orphan workspace / data directories left behind by the
    # pre-fix delete-agent path (no corresponding agents.json record).
    # Runs before _ensure_default_agent so a fully-empty store correctly
    # triggers default-agent seeding.
    _purge_orphan_workspace_dirs(store)

    # App-scoped chat runtime state (checkpointer + agent graph cache).
    # Kept on app.state — never in module-level globals — so each app
    # instance owns its mutable chat state.
    app.state.chat_state = chat_router.ChatState()

    # Ensure a Default Agent (with bootstrap.md first-run guidance) exists
    # on a fresh install — idempotent across restarts.
    await _ensure_default_agent(agent_manager, store)

    # Seed the global skill pool with sample skills (idempotent).
    skills_router.ensure_skill_pool()

    # Mount on app.state for dependency injection into routers (e.g. chat).
    app.state.store = store
    app.state.service = service
    app.state.runtime = runtime
    app.state.factory = factory
    app.state.agent_manager = agent_manager

    # Cron job scheduler — resolves store / agent_manager / factory via
    # app.state at execution time, so it must be created after the
    # bindings above.  Loads persisted jobs from .agentcore/cron_jobs.json
    # and registers them with the APScheduler loop.
    app.state.cron_manager = CronManager(app.state)

    # Scheduled heartbeats run as CronManager *internal* jobs — register
    # them before start() so the scheduler picks them up immediately.
    from agentcore.runtime.heartbeat import sync_all_heartbeat_jobs

    await sync_all_heartbeat_jobs(
        app.state.cron_manager,
        app.state,
        sorted(store.agent_states.keys()),
    )

    # Background memory consolidation runs as CronManager internal jobs
    # too — same startup hook pattern as the heartbeat.
    from agentcore.runtime.memory_consolidation import sync_all_memory_jobs

    await sync_all_memory_jobs(
        app.state.cron_manager,
        app.state,
        sorted(store.agent_states.keys()),
    )

    # Stale background approvals (heartbeat / cron / memory consolidation)
    # expire after APPROVAL_EXPIRY_DAYS — daily internal job, same hook.
    from agentcore.runtime.approval_cleanup import register_approval_cleanup_job

    register_approval_cleanup_job(app.state.cron_manager, app.state)

    await app.state.cron_manager.start()

    # --- Sandbox session manager (optional) ---
    from agentcore.sandbox.config import SandboxGlobalConfig

    sandbox_config = SandboxGlobalConfig.load_effective()
    if sandbox_config.enabled:
        from agentcore.sandbox.lifecycle import SandboxSessionManager

        app.state.sandbox_session_manager = SandboxSessionManager(sandbox_config)
        # Drives check_idle(): pause-on-idle / ephemeral strategies depend
        # on this periodic loop — nothing else calls check_idle().
        app.state.sandbox_session_manager.start_idle_checker()
        logger.info(
            "Sandbox module enabled — server at %s",
            sandbox_config.base_url,
        )
    else:
        app.state.sandbox_session_manager = None
        logger.info("Sandbox module disabled (SANDBOX_ENABLED != true)")

    yield

    # --- Shutdown ----------------------------------------------------------
    await app.state.cron_manager.stop()

    # Destroy all sandbox sessions on shutdown (if enabled)
    sandbox_mgr = getattr(app.state, "sandbox_session_manager", None)
    if sandbox_mgr is not None:
        await sandbox_mgr.stop_idle_checker()
        await sandbox_mgr.destroy_all()

    await agent_manager.shutdown_all()
    await chat_router.close_checkpointer(
        chat_router.get_chat_state(app.state)
    )


app = FastAPI(title="AgentCore Control Plane", version="0.2.0", lifespan=lifespan)

# Sub-path prefix stripping — must wrap the whole app so every router,
# the SPA static mount and /health all see prefix-free paths.
app.add_middleware(BasePathMiddleware)

# Single-user opt-in auth (AGENTCORE_AUTH_ENABLED).  Added after the
# base-path middleware so it enforces on prefix-stripped paths; a
# zero-overhead pass-through while disabled (default local behaviour).
app.add_middleware(AuthMiddleware)

# Default state bindings — keep the app usable even without lifespan
# (e.g. synchronous test clients).
app.state.store = store
app.state.service = service
app.state.factory = factory
app.state.agent_manager = agent_manager

# Register the single-user auth router (/api/auth/*).
app.include_router(auth_router.router)

# Register the chat router — its endpoints resolve store via request.app.state.
app.include_router(chat_router.router)

# Register the agent management router (/api/agents/*).
app.include_router(agent_router.router)

# Register the workspace file management router (/api/agents/{id}/files/*).
app.include_router(files_router.router)

# Register the built-in tools management router (/api/agents/{id}/tools).
app.include_router(tools_router.router)

# Register the environment variables router (/api/envs).
app.include_router(envs_router.router)

# Register the token usage router (/api/token-usage).
app.include_router(token_router.router)

# Register the statistics router (/api/stats).
app.include_router(stats_router.router)

# Register the model management router (/api/models).
app.include_router(models_router.router)

# Register the MCP server management router (/api/agents/{id}/mcp).
app.include_router(mcp_router.router)

# Register the skill pool router (/api/skills).
app.include_router(skills_router.router)

# Register the global security settings router (/api/security).
app.include_router(security_router.router)

# Register the heartbeat router (/api/agents/{id}/heartbeat).
app.include_router(heartbeat_router.router)

# Register the memory router (/api/agents/{id}/memory).
app.include_router(memory_router.router)

# Register the cron job management router (/api/cron).
app.include_router(cron_router.router)

# Register the debug router (/api/debug) — backend log file viewer.
app.include_router(debug_router.router)

# Register the sandbox management router (/api/sandbox/*) — only when enabled.
try:
    from agentcore.sandbox.router import router as sandbox_router

    app.include_router(sandbox_router)
except ImportError:
    pass  # sandbox module not installed


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/navigation")
def navigation() -> list[dict[str, object]]:
    return [asdict(item) for item in default_navigation_domains()]


# ---------------------------------------------------------------------------
# Frontend hosting
#
# When a built console exists (``console/dist``), the backend hosts it
# directly (production mode, SPA fallback included).  Otherwise a minimal
# built-in index page is served and the Vite dev server is expected to run
# alongside the API.
# ---------------------------------------------------------------------------

_CONSOLE_DIST = Path(__file__).resolve().parents[2] / "console" / "dist"


class _SpaStaticFiles(StaticFiles):
    """SPA-aware static files host.

    - Unknown GET paths fall back to ``index.html`` (client-side routing),
      except for ``api/*`` paths which keep plain 404 semantics.
    - Non-GET requests to unknown paths answer 404 (not 405), so
      legacy/removed API endpoints still look like 404s even though the
      SPA mount sits at ``/``.
    """

    async def get_response(self, path: str, scope):
        try:
            resp = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            method = scope.get("method", "GET").upper()
            if exc.status_code == 404 and method in {"GET", "HEAD"}:
                if path.startswith("api"):
                    raise
                resp = await super().get_response("index.html", scope)
            elif exc.status_code == 405:
                raise StarletteHTTPException(status_code=404) from exc
            else:
                raise
        return self._inject_index(resp)

    @staticmethod
    def _inject_index(resp):
        """Serve index.html with the runtime base path baked in.

        The built bundle references assets via the ``__AGENTCORE_BASE_PATH__``
        placeholder (Vite ``base``); it is rewritten per request so a single
        image works under any mount prefix.
        """
        file_path = getattr(resp, "path", None)
        if file_path and Path(file_path).name == "index.html":
            html = Path(file_path).read_text(encoding="utf-8")
            return HTMLResponse(_inject_base_path(html))
        return resp


@app.get("/", response_class=HTMLResponse, response_model=None)
def index() -> Response:
    dist_index = _CONSOLE_DIST / "index.html"
    if dist_index.is_file():
        return HTMLResponse(
            _inject_base_path(dist_index.read_text(encoding="utf-8"))
        )
    html_path = Path(__file__).parent / "web" / "index.html"
    return HTMLResponse(_inject_base_path(html_path.read_text(encoding="utf-8")))


if _CONSOLE_DIST.is_dir():
    # Registered after all API routers so /api/*, /health etc. keep
    # priority; ``html=True`` provides index.html fallback for SPA routes.
    app.mount(
        "/",
        _SpaStaticFiles(directory=_CONSOLE_DIST, html=True),
        name="console-dist",
    )
