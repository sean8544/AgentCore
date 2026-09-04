"""Sandbox session lifecycle management.

Manages the full lifecycle of sandbox instances per agent:
creation, renewal, pause/resume, idle detection, and destruction.

Three strategies are supported (see :class:`SandboxLifecycleConfig`):

- **ephemeral** — sandbox is destroyed when the agent stops.
- **pause-on-idle** — sandbox is paused after idle timeout and resumed
  on next chat.
- **persistent** — sandbox lives until the agent is deleted.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentcore.runtime.sandbox import SandboxUnavailableError
from agentcore.sandbox.config import SandboxGlobalConfig, SandboxLifecycleConfig

logger = logging.getLogger(__name__)


@dataclass
class SandboxSession:
    """Tracks one agent's sandbox instance."""

    sandbox_id: str
    agent_id: str
    backend: Any
    created_at: datetime
    last_active_at: datetime
    status: str = "running"  # running | paused | destroyed | error
    resume_count: int = 0
    lifecycle_config: SandboxLifecycleConfig = field(
        default_factory=SandboxLifecycleConfig,
    )
    sandbox_info: Any = None  # SandboxInfo from client
    image: str = ""  # Container image reference
    backend_cfg: dict[str, Any] = field(default_factory=dict)
    """Snapshot of the agent ``settings["backend"]`` dict this sandbox was
    created from.  Restart / resume-fallback paths reuse it so the image and
    provider survive recreation."""
    _sandbox_recreated: bool = False  # True if sandbox was recreated due to death
    _provider: str = ""  # Provider name (opensandbox / aio) for backend refresh


class SandboxSessionManager:
    """Manages all active sandbox sessions.

    Thread-safe via asyncio.Lock.  Designed to be called from:

    - ``chat_router.resolve_agent()`` → :meth:`get_or_create`
    - ``agent_router`` delete/stop → :meth:`on_agent_stop` / :meth:`destroy`
    - FastAPI shutdown → :meth:`destroy_all` (+ :meth:`stop_idle_checker`)
    - Background idle detector → :meth:`check_idle`, driven by the
      :meth:`start_idle_checker` loop (api.py lifespan / ``POST /start``)
    """

    def __init__(
        self,
        global_config: SandboxGlobalConfig | None = None,
        *,
        idle_check_interval: int = 30,
    ) -> None:
        self._global_config = global_config or SandboxGlobalConfig.from_env()
        self._sessions: dict[str, SandboxSession] = {}
        self._lock = asyncio.Lock()
        self._idle_check_interval = idle_check_interval
        self._idle_task: asyncio.Task[None] | None = None

    # -- public API --------------------------------------------------------

    async def get_or_create(
        self,
        agent_id: str,
        backend_cfg: dict[str, Any],
    ) -> SandboxSession:
        """Return an existing session or create a new one.
    
        Handles resume for paused sessions (any strategy) and renewal
        based on ``renew_on_chat``.
    
        Pause-aware: before declaring a "running" session dead via the
        expensive ``Sandbox.connect()`` health check, the server-side
        status is queried.  If the sandbox was paused (e.g. by the idle
        checker between two chats), it is resumed instead of destroyed
        and recreated.
        """
        async with self._lock:
            session = self._sessions.get(agent_id)
    
        if session is None:
            return await self._create_session(agent_id, backend_cfg)
    
        # ---- Paused session → resume (any strategy) -----------------
        if session.status == "paused":
            session = await self._resume(session)
            session.last_active_at = datetime.now(timezone.utc)
            return session
    
        # ---- Running session → verify + renew -----------------------
        if session.status == "running":
            # Fast server-side status check catches pauses that the
            # idle checker applied but our in-memory session hasn't
            # been updated for yet.
            remote_status = await self._get_remote_status(session)
            if remote_status == "paused":
                session.status = "paused"
                session = await self._resume(session)
                session.last_active_at = datetime.now(timezone.utc)
                return session
    
            if remote_status is None:
                # Cannot determine status — treat as dead
                logger.warning(
                    "Sandbox %s for agent %s unreachable — recreating",
                    session.sandbox_id, agent_id,
                )
                await self.destroy(agent_id)
                new_session = await self._create_session(agent_id, backend_cfg)
                new_session._sandbox_recreated = True
                return new_session
    
            # Sandbox is alive — renew if configured
            async with self._lock:
                lifecycle = session.lifecycle_config
                if lifecycle.renew_on_chat:
                    renew_ok = await self._renew(session)
                    if not renew_ok:
                        logger.warning(
                            "Sandbox %s for agent %s renewal failed — recreating",
                            session.sandbox_id, agent_id,
                        )
                        await self.destroy(agent_id)
                        new_session = await self._create_session(agent_id, backend_cfg)
                        new_session._sandbox_recreated = True
                        return new_session
                session.last_active_at = datetime.now(timezone.utc)
                return session
    
        # ---- Destroyed / error → recreate ---------------------------
        await self.destroy(agent_id)
        new_session = await self._create_session(agent_id, backend_cfg)
        new_session._sandbox_recreated = True
        return new_session

    async def destroy(self, agent_id: str) -> None:
        """Destroy the sandbox for the given agent."""
        async with self._lock:
            session = self._sessions.pop(agent_id, None)
            if session is None:
                return
            await self._do_destroy(session)

    async def destroy_all(self) -> None:
        """Destroy all sandbox sessions (called on shutdown)."""
        async with self._lock:
            agent_ids = list(self._sessions.keys())

        for agent_id in agent_ids:
            await self.destroy(agent_id)

    async def on_agent_stop(self, agent_id: str) -> None:
        """Called when an agent is stopped/unloaded.

        Strategy A/B: destroy the sandbox.
        Strategy C (persistent): keep it running.
        """
        session = self._sessions.get(agent_id)
        if session is None:
            return

        if session.lifecycle_config.strategy == "persistent":
            logger.info(
                "Agent %s stopped but sandbox kept alive (persistent strategy)",
                agent_id,
            )
            return

        await self.destroy(agent_id)

    async def pause(self, agent_id: str) -> None:
        """Manually pause a sandbox (strategy B)."""
        async with self._lock:
            session = self._sessions.get(agent_id)
            if session is None or session.status != "running":
                return
            await self._pause(session)

    async def resume(self, agent_id: str) -> SandboxSession:
        """Manually resume a paused sandbox."""
        async with self._lock:
            session = self._sessions.get(agent_id)
            if session is None or session.status != "paused":
                raise SandboxUnavailableError(
                    f"No paused sandbox session for agent {agent_id}"
                )
            return await self._resume(session)

    async def renew(self, agent_id: str, extra_seconds: int = 600) -> None:
        """Manually renew a sandbox timeout."""
        session = self._sessions.get(agent_id)
        if session is None or session.status != "running":
            return
        await self._renew(session, extra_seconds=extra_seconds)

    async def restart(self, agent_id: str, backend_cfg: dict[str, Any]) -> SandboxSession:
        """Destroy and recreate a sandbox."""
        await self.destroy(agent_id)
        return await self.get_or_create(agent_id, backend_cfg)

    async def on_execute(self, agent_id: str) -> None:
        """Called when the execute tool is invoked inside a sandbox."""
        session = self._sessions.get(agent_id)
        if session is None or session.status != "running":
            return
        session.last_active_at = datetime.now(timezone.utc)
        if session.lifecycle_config.renew_on_execute:
            await self._renew(session)

    def list_sessions(self) -> list[SandboxSession]:
        """Return a snapshot of all active sessions."""
        return list(self._sessions.values())

    def get_session(self, agent_id: str) -> SandboxSession | None:
        return self._sessions.get(agent_id)

    async def health_check(self) -> dict[str, Any]:
        """Check if the OpenSandbox server is reachable."""
        try:
            from agentcore.sandbox.client import OpenSandboxClient

            client = OpenSandboxClient(self._global_config)
            ok = await client.health_check()
            return {
                "server_reachable": ok,
                "server_url": self._global_config.base_url,
                "active_sessions": len(self._sessions),
                "enabled": self._global_config.enabled,
            }
        except Exception as exc:
            return {
                "server_reachable": False,
                "server_url": self._global_config.base_url,
                "active_sessions": len(self._sessions),
                "enabled": self._global_config.enabled,
                "error": str(exc),
            }

    async def check_idle(self) -> None:
        """Background task: check all sessions for idle timeout.

        Should be called periodically (e.g. every 30 seconds) by a
        background scheduler.
        """
        now = datetime.now(timezone.utc)
        for agent_id, session in list(self._sessions.items()):
            if session.status != "running":
                continue
            lifecycle = session.lifecycle_config
            idle_seconds = (now - session.last_active_at).total_seconds()

            if lifecycle.strategy == "ephemeral":
                if idle_seconds > lifecycle.idle_timeout:
                    logger.info(
                        "Sandbox %s idle for %.0fs (> %ds) — destroying",
                        agent_id, idle_seconds, lifecycle.idle_timeout,
                    )
                    await self.destroy(agent_id)

            elif lifecycle.strategy == "pause-on-idle":
                if idle_seconds > lifecycle.idle_timeout:
                    logger.info(
                        "Sandbox %s idle for %.0fs (> %ds) — pausing",
                        agent_id, idle_seconds, lifecycle.idle_timeout,
                    )
                    async with self._lock:
                        await self._pause(session)

            # persistent: do nothing on idle

    # -- internal helpers --------------------------------------------------

    async def _create_session(
        self,
        agent_id: str,
        backend_cfg: dict[str, Any],
    ) -> SandboxSession:
        """Create a new sandbox session using the configured provider."""
        from agentcore.runtime.sandbox import get_sandbox_factory

        lifecycle = SandboxLifecycleConfig.from_dict(
            backend_cfg.get("lifecycle")
        )
        provider = backend_cfg.get("provider", "opensandbox")

        # Auto-detect AIO provider from image name to prevent misconfiguration.
        # If the image looks like the AIO image but provider is not "aio",
        # automatically switch to the AIO factory.
        image = backend_cfg.get("image", "")
        if image and "agent-infra/sandbox" in image and provider != "aio":
            logger.info(
                "Auto-detecting AIO provider from image %r for agent %s "
                "(was %r, switching to 'aio')",
                image, agent_id, provider,
            )
            provider = "aio"

        factory = get_sandbox_factory(provider)

        # Bind the global config to the factory if it supports it
        if hasattr(factory, '_config'):
            factory._config = self._global_config

        # Determine default image based on provider
        default_image = "python:3.12-slim"
        if provider == "aio":
            from agentcore.sandbox.factory import AIO_DEFAULT_IMAGE
            default_image = AIO_DEFAULT_IMAGE

        try:
            backend = await factory.create_async(
                agent_id=agent_id,
                image=backend_cfg.get("image", default_image),
                timeout=lifecycle.timeout,
            )
        except SandboxUnavailableError:
            raise
        except Exception as exc:
            raise SandboxUnavailableError(
                f"Failed to create sandbox for agent {agent_id}: {exc}"
            ) from exc

        sandbox_id = getattr(backend, "id", f"unknown-{agent_id}")

        session = SandboxSession(
            sandbox_id=sandbox_id,
            agent_id=agent_id,
            backend=backend,
            created_at=datetime.now(timezone.utc),
            last_active_at=datetime.now(timezone.utc),
            status="running",
            lifecycle_config=lifecycle,
            image=backend_cfg.get("image", ""),
            backend_cfg=dict(backend_cfg),
            _provider=provider,
        )
        self._sessions[agent_id] = session

        logger.info(
            "Created sandbox session %s for agent %s (strategy=%s, timeout=%ds)",
            sandbox_id, agent_id, lifecycle.strategy, lifecycle.timeout,
        )
        return session

    async def _renew(
        self, session: SandboxSession, *, extra_seconds: int | None = None,
    ) -> bool:
        """Extend the sandbox timeout.
        
        Returns True if renewal succeeded, False if the sandbox is unreachable.
        """
        try:
            from agentcore.sandbox.client import OpenSandboxClient

            client = OpenSandboxClient(self._global_config)
            timeout = extra_seconds or session.lifecycle_config.timeout
            await client.renew_sandbox(session.sandbox_id, timeout)
            session.last_active_at = datetime.now(timezone.utc)
            logger.debug(
                "Renewed sandbox %s for agent %s (+%ds)",
                session.sandbox_id, session.agent_id, timeout,
            )
            return True
        except Exception as exc:
            logger.warning(
                "Failed to renew sandbox %s: %s",
                session.sandbox_id, exc,
            )
            return False

    async def _pause(self, session: SandboxSession) -> None:
        """Pause a running sandbox (strategy B)."""
        try:
            from agentcore.sandbox.client import OpenSandboxClient

            client = OpenSandboxClient(self._global_config)
            await client.pause_sandbox(session.sandbox_id)
            session.status = "paused"
            logger.info(
                "Paused sandbox %s for agent %s",
                session.sandbox_id, session.agent_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to pause sandbox %s: %s",
                session.sandbox_id, exc,
            )

    async def _resume(self, session: SandboxSession) -> SandboxSession:
        """Resume a paused sandbox and refresh the backend.

        After resume the sandbox receives new endpoint URLs; the old
        backend object (which captured the pre-pause endpoints) is
        replaced via :meth:`_recreate_backend` so subsequent tool calls
        reach the correct address.
        """
        try:
            from agentcore.sandbox.client import OpenSandboxClient

            client = OpenSandboxClient(self._global_config)
            await client.resume_sandbox(session.sandbox_id)
            session.status = "running"
            session.resume_count += 1
            session.last_active_at = datetime.now(timezone.utc)
            # Refresh backend — endpoints change after pause/resume
            new_backend = await self._recreate_backend(session)
            if new_backend is not None:
                session.backend = new_backend
            logger.info(
                "Resumed sandbox %s for agent %s (resume_count=%d)",
                session.sandbox_id, session.agent_id, session.resume_count,
            )
            return session
        except Exception as exc:
            logger.warning(
                "Failed to resume sandbox %s: %s — will recreate",
                session.sandbox_id, exc,
            )
            # If resume fails, destroy and recreate — reuse the original
            # backend config snapshot so image/provider survive recreation.
            await self._do_destroy(session)
            backend_cfg: dict[str, Any] = session.backend_cfg or {
                "lifecycle": session.lifecycle_config.to_dict(),
            }
            return await self._create_session(
                session.agent_id, backend_cfg,
            )

    async def _do_destroy(self, session: SandboxSession) -> None:
        """Actually destroy a sandbox container."""
        try:
            from agentcore.sandbox.client import OpenSandboxClient

            client = OpenSandboxClient(self._global_config)
            await client.destroy_sandbox(session.sandbox_id)
            session.status = "destroyed"
            logger.info(
                "Destroyed sandbox %s for agent %s",
                session.sandbox_id, session.agent_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to destroy sandbox %s: %s",
                session.sandbox_id, exc,
            )
            session.status = "destroyed"

    async def _get_remote_status(self, session: SandboxSession) -> str | None:
        """Query the server for the sandbox's actual status.

        Uses the manager ``list_sandbox_infos`` API which is a single
        HTTP call — much faster than ``Sandbox.connect()`` (which resolves
        endpoints and waits for readiness, timing out on paused containers).

        Returns the normalised status string, or ``None`` when the sandbox
        cannot be found or the query fails.
        """
        try:
            from agentcore.sandbox.client import OpenSandboxClient

            client = OpenSandboxClient(self._global_config)
            sandboxes = await client.list_sandboxes()
            for sb in sandboxes:
                if sb.sandbox_id == session.sandbox_id:
                    return sb.status
            return None  # Not found server-side → dead
        except Exception as exc:
            logger.debug(
                "Remote status query for sandbox %s failed: %s",
                session.sandbox_id, exc,
            )
            return None

    async def _recreate_backend(
        self, session: SandboxSession,
    ) -> Any | None:
        """Re-create the sandbox backend after resume.

        Pause/resume changes the container's endpoint URLs.  The old
        backend object still holds the pre-pause endpoints, so all
        subsequent operations would fail.  This method creates a fresh
        backend connected to the resumed container.

        Returns the new backend, or ``None`` on failure (the old backend
        is kept as a fallback).
        """
        try:
            from agentcore.runtime.sandbox import get_sandbox_factory

            provider = session._provider or "opensandbox"
            factory = get_sandbox_factory(provider)
            if hasattr(factory, '_config'):
                factory._config = self._global_config

            image = session.image or session.backend_cfg.get("image", "")
            if not image:
                image = "python:3.12-slim"
                if provider == "aio":
                    from agentcore.sandbox.factory import AIO_DEFAULT_IMAGE
                    image = AIO_DEFAULT_IMAGE

            timeout = session.lifecycle_config.timeout

            # For AIO: connect to the existing sandbox instead of creating new
            if provider == "aio":
                from opensandbox import SandboxSync
                from opensandbox.config import ConnectionConfigSync
                from agentcore.sandbox.aio_sandbox import AIOSandboxBackend

                conn_config = ConnectionConfigSync(
                    domain=self._global_config.domain,
                    protocol=self._global_config.protocol,
                    api_key=self._global_config.api_key,
                )
                sandbox = SandboxSync.connect(
                    session.sandbox_id, connection_config=conn_config,
                )
                endpoint = sandbox.get_endpoint(8080)
                base_url: str = endpoint.endpoint
                if base_url and not base_url.startswith(('http://', 'https://')):
                    base_url = f'http://{base_url}'
                headers: dict[str, str] = dict(endpoint.headers) if endpoint.headers else {}
                return AIOSandboxBackend(
                    sandbox_id=session.sandbox_id,
                    base_url=base_url,
                    headers=headers,
                    timeout=timeout,
                )
            else:
                # OpenSandbox: reconnect the SandboxSync wrapper
                from opensandbox import SandboxSync
                from opensandbox.config import ConnectionConfigSync
                from langchain_opensandbox import OpenSandboxBackend

                conn_config = ConnectionConfigSync(
                    domain=self._global_config.domain,
                    protocol=self._global_config.protocol,
                    api_key=self._global_config.api_key,
                )
                sandbox = SandboxSync.connect(
                    session.sandbox_id, connection_config=conn_config,
                )
                return OpenSandboxBackend(sandbox=sandbox, timeout=timeout)
        except Exception as exc:
            logger.warning(
                "Failed to recreate backend for sandbox %s: %s — "
                "keeping old backend (may be stale)",
                session.sandbox_id, exc,
            )
            return None

    async def _check_sandbox_alive(self, session: SandboxSession) -> bool:
        """Check if the sandbox container is still running.

        .. deprecated::
            This method uses ``Sandbox.connect()`` which blocks for up to
            30 seconds on paused containers.  Prefer :meth:`_get_remote_status`
            for fast server-side status checks.  This method is kept as a
            last-resort fallback.

        Returns True if the sandbox is alive, False otherwise.
        """
        try:
            from opensandbox import Sandbox
            from opensandbox.config import ConnectionConfig

            conn = ConnectionConfig(
                domain=self._global_config.domain,
                protocol=self._global_config.protocol,
            )
            sandbox = await Sandbox.connect(
                session.sandbox_id,
                connection_config=conn,
                skip_health_check=True,
            )
            backend = session.backend
            provider = getattr(backend, '_provider', None)
            if provider == 'aio' or type(backend).__name__ == 'AIOSandboxBackend':
                health_port = 8080
            else:
                health_port = 18080
            await sandbox.get_endpoint(health_port)
            return True
        except Exception as exc:
            logger.debug(
                "Sandbox %s health check failed: %s",
                session.sandbox_id, exc,
            )
            return False

    # -- background idle scheduler ------------------------------------------

    def start_idle_checker(self) -> None:
        """Launch the periodic idle-detection task (idempotent).

        Without this, ``pause-on-idle`` / ``ephemeral`` strategies never fire
        — :meth:`check_idle` has no other caller.
        """
        if self._idle_task is not None and not self._idle_task.done():
            return
        self._idle_task = asyncio.create_task(self._idle_loop())
        logger.info(
            "Sandbox idle checker started (interval=%ds)",
            self._idle_check_interval,
        )

    async def stop_idle_checker(self) -> None:
        """Cancel the idle-detection task and await its completion."""
        task = self._idle_task
        self._idle_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Idle checker raised on shutdown", exc_info=True)

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(self._idle_check_interval)
            try:
                await self.check_idle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Sandbox idle check iteration failed")
