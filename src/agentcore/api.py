"""FastAPI application for AgentCore control plane."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentcore.menu import default_navigation_domains
from agentcore.repository import ControlPlaneStore
from agentcore.runtime import paths
from agentcore.runtime.agent_factory import AgentFactory
from agentcore.runtime.agent_runtime import AgentRuntime
from agentcore.runtime.multi_agent_manager import (
    MultiAgentManager,
    migrate_workspace_layout,
)
from agentcore.runtime import agent_router, chat_router, files_router
from agentcore.runtime import envs_router, models_router, stats_router
from agentcore.runtime import mcp_router, skills_router
from agentcore.runtime import security_router
from agentcore.runtime import token_router, tools_router
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: initialise shared resources on startup."""
    global store, service, runtime, factory, agent_manager

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

    yield

    # --- Shutdown ----------------------------------------------------------
    await agent_manager.shutdown_all()
    await chat_router.close_checkpointer(
        chat_router.get_chat_state(app.state)
    )


app = FastAPI(title="AgentCore Control Plane", version="0.2.0", lifespan=lifespan)

# Default state bindings — keep the app usable even without lifespan
# (e.g. synchronous test clients).
app.state.store = store
app.state.service = service
app.state.factory = factory
app.state.agent_manager = agent_manager

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
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            method = scope.get("method", "GET").upper()
            if exc.status_code == 404 and method in {"GET", "HEAD"}:
                if path.startswith("api"):
                    raise
                return await super().get_response("index.html", scope)
            if exc.status_code == 405:
                raise StarletteHTTPException(status_code=404) from exc
            raise


@app.get("/", response_class=HTMLResponse, response_model=None)
def index() -> Response:
    dist_index = _CONSOLE_DIST / "index.html"
    if dist_index.is_file():
        return FileResponse(dist_index)
    html_path = Path(__file__).parent / "web" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


if _CONSOLE_DIST.is_dir():
    # Registered after all API routers so /api/*, /health etc. keep
    # priority; ``html=True`` provides index.html fallback for SPA routes.
    app.mount(
        "/",
        _SpaStaticFiles(directory=_CONSOLE_DIST, html=True),
        name="console-dist",
    )
