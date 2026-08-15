"""Multi-Agent manager: manages multiple Agent Workspace instances.

Provides centralized management for multiple :class:`Workspace` objects,
including lazy loading, lifecycle management, hot reloading, and
concurrency-safe access patterns.

The manager sits alongside :class:`~agentcore.runtime.agent_runtime.AgentRuntime`
in the application stack:

* **AgentRuntime** owns agent *graphs* — invocation, streaming, state machine.
* **MultiAgentManager** owns agent *workspaces* — conversations, drivers,
  plugins, and per-agent directory state.

Both share the same :class:`~agentcore.repository.ControlPlaneStore` for
persistence.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from agentcore.runtime import paths
from agentcore.runtime.workspace import Workspace

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# One-time migration from the legacy ``.agentcore/workspaces/`` layout
# ---------------------------------------------------------------------------


def _move_with_retry(src: Path, dst: Path, attempts: int = 3) -> None:
    """Move *src* to *dst*, retrying briefly on Windows file-lock errors."""
    for attempt in range(1, attempts + 1):
        try:
            shutil.move(str(src), str(dst))
            return
        except OSError:
            if attempt >= attempts:
                raise
            logger.warning(
                "Move %s -> %s failed (attempt %d/%d); retrying …",
                src,
                dst,
                attempt,
                attempts,
            )
            time.sleep(0.2 * attempt)


def migrate_workspace_layout() -> None:
    """一次性迁移旧目录结构 ``.agentcore/workspaces/`` → 新布局。

    幂等：旧目录不存在或已迁移时直接返回；迁移失败只记录警告，
    不阻塞启动（旧目录保留，可下次重试）。

    迁移内容：

    * ``sessions.json`` 从旧 workspace 目录搬到
      ``.agentcore/data/agents/{agent_id}/sessions/``；
    * 其余 workspace 文件搬到 ``.agentcore/workspace/agent/{agent_id}/``。
    """
    old_base = paths.get_data_dir() / "workspaces"
    if not old_base.exists():
        return

    new_base = paths.get_workspace_root()
    try:
        new_base.mkdir(parents=True, exist_ok=True)
        for agent_dir in list(old_base.iterdir()):
            if not agent_dir.is_dir():
                continue
            agent_id = agent_dir.name

            # 1) sessions.json 迁出到 data 目录（agent 不可见）。
            old_sessions = agent_dir / "sessions.json"
            if old_sessions.exists():
                target_sessions = (
                    paths.get_agent_sessions_dir(agent_id) / "sessions.json"
                )
                if not target_sessions.exists():
                    target_sessions.parent.mkdir(parents=True, exist_ok=True)
                    _move_with_retry(old_sessions, target_sessions)
                else:
                    old_sessions.unlink(missing_ok=True)

            # 2) workspace 目录搬到新布局。
            target = new_base / agent_id
            if not target.exists():
                _move_with_retry(agent_dir, target)
            elif not any(agent_dir.iterdir()):
                agent_dir.rmdir()

        if not any(old_base.iterdir()):
            old_base.rmdir()
        logger.info("Workspace layout migration completed (%s -> %s)", old_base, new_base)
    except Exception:
        logger.exception(
            "Workspace layout migration failed — old layout kept at %s; "
            "startup continues",
            old_base,
        )


class MultiAgentManager:
    """Manage multiple Agent :class:`Workspace` instances.

    Features
    --------
    * **Lazy loading** — workspaces are created on first request, not
      pre-loaded at startup.
    * **Concurrency safety** — a global lock protects the workspace
      registry; per-agent locks protect individual workspace operations;
      a semaphore caps the number of simultaneous workspace creations.
    * **Hot reload** — individual workspaces can be reloaded without
      affecting others.
    * **Graceful shutdown** — :meth:`shutdown_all` flushes every workspace
      and releases resources.
    """

    def __init__(
        self,
        factory: Any,
        repository: Any,
        workspace_base_dir: Path | None = None,
        max_concurrent: int = 10,
        chat_manager: Any = None,
        chat_router: Any = None,
        mcp_manager: Any = None,
        driver_manager: Any = None,
        plugin_registry: Any = None,
    ) -> None:
        """Initialise the manager.

        Parameters
        ----------
        factory:
            An :class:`~agentcore.runtime.agent_factory.AgentFactory` (or
            compatible object) used when workspace creation requires agent
            graph materialisation.
        repository:
            A :class:`~agentcore.repository.ControlPlaneStore` (or any
            object exposing the same interface).
        workspace_base_dir:
            Root directory under which per-agent workspace directories
            are created.  Defaults to
            :func:`agentcore.runtime.paths.get_workspace_root`
            (``.agentcore/workspace/agent``).
        max_concurrent:
            Maximum number of workspaces that may be created
            simultaneously.  Further callers block on a semaphore.
        chat_manager:
            A :class:`~agentcore.runtime.workspace.ChatManager` (or
            compatible object) forwarded to every :class:`Workspace`.
        chat_router:
            A :class:`~agentcore.runtime.chat_router.ChatRouter` (or
            compatible object) forwarded to every :class:`Workspace`.
        mcp_manager:
            A :class:`~agentcore.runtime.mcp_manager.MCPManager` (or
            compatible object) forwarded to every :class:`Workspace`.
        driver_manager:
            A :class:`~agentcore.runtime.workspace.DriverManager` (or
            compatible object) forwarded to every :class:`Workspace`.
        plugin_registry:
            A :class:`~agentcore.runtime.workspace.PluginRegistry` (or
            compatible object) forwarded to every :class:`Workspace`.
        """
        self._factory = factory
        self._repository = repository
        self._workspace_base_dir = workspace_base_dir or paths.get_workspace_root()
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._chat_manager = chat_manager
        self._chat_router = chat_router
        self._mcp_manager = mcp_manager
        self._driver_manager = driver_manager
        self._plugin_registry = plugin_registry

        # workspace_id (== agent_id) -> Workspace instance
        self._workspaces: dict[str, Workspace] = {}

        # Per-agent locks for fine-grained synchronisation.
        self._agent_locks: dict[str, asyncio.Lock] = {}

        # Global lock protecting _workspaces / _agent_locks dict mutations.
        self._global_lock = asyncio.Lock()

        # Deduplication events: when a workspace is being created, other
        # callers for the same agent_id wait on this event instead of
        # creating a duplicate.
        self._pending_creates: dict[str, asyncio.Event] = {}

        logger.debug(
            "MultiAgentManager initialised (base_dir=%s, max_concurrent=%d)",
            self._workspace_base_dir,
            self._max_concurrent,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _workspace_dir_for(self, agent_id: str) -> Path:
        """Return the on-disk directory for a given agent's workspace."""
        return paths.get_agent_workspace_dir(agent_id)

    def _get_or_create_agent_lock(self, agent_id: str) -> asyncio.Lock:
        """Return the per-agent lock, creating it if necessary.

        Must be called while holding :attr:`_global_lock`.
        """
        lock = self._agent_locks.get(agent_id)
        if lock is None:
            lock = asyncio.Lock()
            self._agent_locks[agent_id] = lock
        return lock

    # ------------------------------------------------------------------
    # Core API — lazy workspace creation
    # ------------------------------------------------------------------

    async def get_or_create_workspace(
        self,
        agent_id: str,
    ) -> Workspace:
        """Return the :class:`Workspace` for *agent_id*, creating it if needed.

        On first call for a given *agent_id* the workspace is constructed,
        its directory is ensured on disk, and :meth:`Workspace.initialize`
        is called.  Subsequent calls return the cached instance.

        Concurrent callers for the same *agent_id* are deduplicated: only
        one creation proceeds while the others wait.

        Parameters
        ----------
        agent_id:
            Unique agent identifier (also used as workspace id).

        Returns
        -------
        Workspace
            The ready-to-use workspace.
        """
        # Fast path — already loaded (no lock).
        ws = self._workspaces.get(agent_id)
        if ws is not None:
            return ws

        # Determine (briefly, under lock) whether we are the creator or
        # should wait on an in-flight creation.  Creation itself happens
        # OUTSIDE the lock — asyncio.Lock is not reentrant and
        # _do_create_workspace must never be called while holding it.
        creator = False
        async with self._global_lock:
            # Re-check under lock.
            ws = self._workspaces.get(agent_id)
            if ws is not None:
                return ws

            if agent_id in self._pending_creates:
                # Another coroutine is creating — wait for it.
                event = self._pending_creates[agent_id]
            else:
                # We are the creator.
                event = asyncio.Event()
                self._pending_creates[agent_id] = event
                creator = True

        if not creator:
            # Wait for the other coroutine to finish creation.
            await event.wait()
            ws = self._workspaces.get(agent_id)
            if ws is None:
                raise RuntimeError(
                    f"Workspace creation for agent {agent_id!r} failed "
                    f"in another coroutine"
                )
            return ws

        # Creator path — global lock released before the actual creation.
        try:
            workspace = await self._do_create_workspace(agent_id)
        except Exception:
            # Unblock waiters and remove the pending entry before re-raising.
            async with self._global_lock:
                self._pending_creates.pop(agent_id, None)
            event.set()
            raise

        # Register the workspace under the lock, then unblock waiters.
        async with self._global_lock:
            self._workspaces[agent_id] = workspace
            self._get_or_create_agent_lock(agent_id)
            self._pending_creates.pop(agent_id, None)
        event.set()
        return workspace

    async def _do_create_workspace(
        self,
        agent_id: str,
    ) -> Workspace:
        """Construct and initialise a workspace and return it.

        Called WITHOUT :attr:`_global_lock` held (asyncio.Lock is not
        reentrant).  Registration into :attr:`_workspaces` is performed
        by the caller.  The semaphore limits how many creations proceed
        in parallel.
        """
        async with self._semaphore:
            workspace_dir = self._workspace_dir_for(agent_id)
            logger.info(
                "Creating workspace for agent %s at %s",
                agent_id,
                workspace_dir,
            )
            try:
                workspace = Workspace(
                    workspace_id=agent_id,
                    agent_id=agent_id,
                    workspace_dir=workspace_dir,
                    repository=self._repository,
                    chat_manager=self._chat_manager,
                    chat_router=self._chat_router,
                    mcp_manager=self._mcp_manager,
                    driver_manager=self._driver_manager,
                    plugin_registry=self._plugin_registry,
                )
                await workspace.initialize()
                logger.info(
                    "Workspace for agent %s created and initialised",
                    agent_id,
                )
                return workspace
            except Exception:
                logger.exception(
                    "Failed to create workspace for agent %s", agent_id
                )
                raise

    # ------------------------------------------------------------------
    # Removal
    # ------------------------------------------------------------------

    async def unload_workspace(self, agent_id: str) -> None:
        """Unload the workspace for *agent_id* from memory — keep disk files.

        The workspace instance is removed from the in-memory registry and
        shut down (flushing sessions and other state to disk), but the
        on-disk workspace directory is **not** deleted — ``rmtree`` only
        ever happens on agent deletion (:meth:`remove_workspace`).  The
        agent can be re-loaded lazily via
        :meth:`get_or_create_workspace` at any time.

        Unknown agent ids are logged and ignored (idempotent unload).
        """
        async with self._global_lock:
            workspace = self._workspaces.pop(agent_id, None)
            self._agent_locks.pop(agent_id, None)

        if workspace is None:
            logger.warning(
                "unload_workspace called for unknown/unloaded agent %s",
                agent_id,
            )
            return

        try:
            await workspace.shutdown()
            logger.info(
                "Workspace for agent %s unloaded from memory "
                "(disk files preserved)",
                agent_id,
            )
        except Exception:
            logger.exception(
                "Error shutting down workspace for agent %s during unload",
                agent_id,
            )

    async def remove_workspace(self, agent_id: str) -> None:
        """Shut down and remove the workspace for *agent_id*.

        If the workspace is not loaded, a warning is logged; the on-disk
        workspace directory (if any) is still cleaned up.  Returns silently
        when there is nothing to remove.

        .. note::
           This is the **only** operation that deletes workspace files
           from disk (``rmtree``) — it backs the ``DELETE agent`` API.
           Use :meth:`unload_workspace` to stop/unload without deleting.
        """
        async with self._global_lock:
            workspace = self._workspaces.pop(agent_id, None)
            self._agent_locks.pop(agent_id, None)

        if workspace is None:
            logger.warning(
                "remove_workspace called for unknown agent %s", agent_id
            )
        else:
            try:
                await workspace.shutdown()
                logger.info("Workspace for agent %s shut down and removed", agent_id)
            except Exception:
                logger.exception(
                    "Error shutting down workspace for agent %s", agent_id
                )

        # 清理磁盘上的工作区目录（即使 workspace 未加载也执行）。
        workspace_dir = self._workspace_dir_for(agent_id)
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir, ignore_errors=True)
            logger.info("Removed workspace directory for agent %s", agent_id)

    # ------------------------------------------------------------------
    # Hot reload
    # ------------------------------------------------------------------

    async def reload_workspace(self, agent_id: str) -> None:
        """Hot-reload the workspace for *agent_id*.

        Calls :meth:`Workspace.reload` which re-reads persisted data from
        disk without a full shutdown / restart cycle.  Other workspaces
        are not affected.

        If the workspace does not exist, a warning is logged.
        """
        workspace = self._workspaces.get(agent_id)
        if workspace is None:
            logger.warning(
                "reload_workspace called for unknown agent %s", agent_id
            )
            return

        agent_lock = self._agent_locks.get(agent_id)
        if agent_lock is not None:
            async with agent_lock:
                await self._do_reload(workspace, agent_id)
        else:
            await self._do_reload(workspace, agent_id)

    async def _do_reload(self, workspace: Workspace, agent_id: str) -> None:
        """Perform the actual reload with logging."""
        logger.info("Reloading workspace for agent %s", agent_id)
        try:
            await workspace.reload()
            logger.info("Workspace for agent %s reloaded successfully", agent_id)
        except Exception:
            logger.exception(
                "Failed to reload workspace for agent %s", agent_id
            )
            raise

    async def reload_all(self) -> None:
        """Hot-reload all currently loaded workspaces.

        Workspaces are reloaded concurrently.  Errors in individual
        workspaces are logged but do not prevent others from reloading.
        """
        agent_ids = list(self._workspaces.keys())
        if not agent_ids:
            logger.info("No workspaces loaded — nothing to reload")
            return

        logger.info("Reloading all %d workspace(s)", len(agent_ids))

        async def _safe_reload(aid: str) -> None:
            try:
                await self.reload_workspace(aid)
            except Exception:
                logger.exception(
                    "Error during reload_all for agent %s", aid
                )

        await asyncio.gather(*(_safe_reload(aid) for aid in agent_ids))
        logger.info("reload_all completed for %d workspace(s)", len(agent_ids))

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_workspace(self, agent_id: str) -> Workspace | None:
        """Return the workspace for *agent_id*, or ``None`` if not loaded."""
        return self._workspaces.get(agent_id)

    def list_workspaces(self) -> list[str]:
        """Return all currently loaded workspace IDs."""
        return list(self._workspaces.keys())

    def get_loaded_workspaces(self) -> dict[str, Workspace]:
        """Return a snapshot of all loaded workspaces (agent_id → Workspace).

        The returned dict is a shallow copy — safe to iterate while
        other workspaces are being created/removed concurrently.
        """
        return dict(self._workspaces)

    def is_workspace_loaded(self, agent_id: str) -> bool:
        """Check whether a workspace is currently loaded for *agent_id*."""
        return agent_id in self._workspaces

    async def get_all_status(self) -> list[dict[str, Any]]:
        """Return a status summary for every loaded workspace."""
        return [ws.get_status() for ws in self._workspaces.values()]

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown_all(self) -> None:
        """Shut down all loaded workspaces and clear internal state.

        Workspaces are shut down concurrently.  Errors in individual
        workspaces are logged but do not prevent others from shutting down.
        """
        if not self._workspaces:
            logger.info("No workspaces to shut down")
            return

        agent_ids = list(self._workspaces.keys())
        logger.info(
            "Shutting down %d workspace(s): %s",
            len(agent_ids),
            agent_ids,
        )

        async def _safe_shutdown(aid: str) -> None:
            ws = self._workspaces.get(aid)
            if ws is None:
                return
            try:
                await ws.shutdown()
                logger.debug("Workspace %s shut down", aid)
            except Exception:
                logger.exception("Error shutting down workspace %s", aid)

        await asyncio.gather(*(_safe_shutdown(aid) for aid in agent_ids))

        # Clear all state.
        async with self._global_lock:
            self._workspaces.clear()
            self._agent_locks.clear()

        logger.info("All workspaces shut down")

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        loaded = list(self._workspaces.keys())
        return (
            f"MultiAgentManager(loaded={len(loaded)}, "
            f"agents={loaded})"
        )
