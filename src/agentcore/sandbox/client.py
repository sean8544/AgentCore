"""OpenSandbox HTTP client.

Wraps the ``opensandbox`` Python SDK to provide a thin async-friendly
interface.  Can be used standalone — does **not** import anything from
``agentcore.runtime``.

When the ``opensandbox`` package is not installed, all methods raise
:class:`ImportError` with a helpful message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from agentcore.sandbox.config import SandboxGlobalConfig

logger = logging.getLogger(__name__)


@dataclass
class SandboxInfo:
    """Lightweight description of a running sandbox."""

    sandbox_id: str
    agent_id: str = ""
    image: str = ""
    status: str = "running"
    created_at: datetime | None = None
    expires_at: datetime | None = None
    platform: str = ""
    timeout_seconds: int = 600
    remaining_seconds: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


def _norm_status(raw: Any) -> str:
    """Normalise an SDK status value to a plain state string.

    Handles ``SandboxStatus`` (exposes ``.state``), enums (``.value``) and bare
    strings.  Never falls back to the model ``repr`` — that leaks the full
    ``state='Running' reason=... datetime(...)`` string into the UI columns.
    """
    if raw is None:
        return "unknown"
    state = getattr(raw, "state", None)
    if state is not None:
        return str(state)
    value = getattr(raw, "value", None)
    if value is not None:
        return str(value)
    return str(raw)


def _norm_platform(raw: Any) -> str:
    """Normalise a ``PlatformSpec`` to ``"os/arch"``; empty when absent."""
    if raw is None:
        return ""
    os_ = getattr(raw, "os", None)
    arch = getattr(raw, "arch", None)
    if os_ is not None or arch is not None:
        return "/".join(str(x) for x in (os_, arch) if x)
    return str(raw)


def _norm_image(raw: Any) -> str:
    """Normalise an SDK image value (SandboxImageSpec / str) to a string."""
    if raw is None:
        return ""
    inner = getattr(raw, "image", None)
    return str(inner if inner is not None else raw)


def _as_datetime(raw: Any) -> datetime | None:
    """Return a datetime when the SDK gave one, else ``None``."""
    return raw if isinstance(raw, datetime) else None


class OpenSandboxClient:
    """OpenSandbox HTTP client.

    Encapsulates the ``opensandbox`` Python SDK.  All public methods are
    ``async`` so they can be called from FastAPI handlers without
    blocking the event loop.
    """

    def __init__(self, config: SandboxGlobalConfig) -> None:
        self._config = config
        self._sdk_available: bool | None = None

    # -- internal helpers --------------------------------------------------

    def _ensure_sdk(self) -> None:
        """Raise ImportError if the opensandbox SDK is not installed."""
        if self._sdk_available is False:
            raise ImportError(
                "The 'opensandbox' package is not installed. "
                "Install it with: pip install agentcore[sandbox]"
            )
        try:
            import opensandbox  # noqa: F401
            self._sdk_available = True
        except ImportError:
            self._sdk_available = False
            raise ImportError(
                "The 'opensandbox' package is not installed. "
                "Install it with: pip install agentcore[sandbox]"
            )

    def _build_connection_config(self) -> Any:
        """Build an SDK ConnectionConfig from our global config."""
        from opensandbox.config import ConnectionConfig

        return ConnectionConfig(
            domain=self._config.domain,
            protocol=self._config.protocol,
            api_key=self._config.api_key,
        )

    async def _open_manager(self) -> Any:
        """Create a :class:`SandboxManager` for server-level lifecycle calls.

        Lifecycle operations (pause/resume/kill/renew) go through the manager
        API instead of ``Sandbox.connect()``: connecting requires resolving a
        live execd endpoint and waiting for readiness, which is impossible (or
        wastes 30s timing out) for PAUSED or dead sandboxes.
        """
        from opensandbox import SandboxManager

        return await SandboxManager.create(
            connection_config=self._build_connection_config(),
        )

    # -- public API --------------------------------------------------------

    async def health_check(self) -> bool:
        """Return ``True`` if the OpenSandbox server is reachable."""
        try:
            self._ensure_sdk()
            from opensandbox import SandboxManager
            from opensandbox.models import SandboxFilter

            conn = self._build_connection_config()
            # Use SandboxManager to list sandboxes as a connectivity probe.
            mgr = await SandboxManager.create(connection_config=conn)
            await mgr.list_sandbox_infos(SandboxFilter())
            await mgr.close()
            return True
        except Exception:
            return False

    async def create_sandbox(
        self,
        *,
        name: str,
        image: str = "python:3.12-slim",
        timeout: int = 600,
        agent_id: str = "",
        env: dict[str, str] | None = None,
    ) -> SandboxInfo:
        """Create a new sandbox container and return its info.

        Parameters
        ----------
        name : str
            Human-readable name (used as Docker container label).
        image : str
            Docker image to use.
        timeout : int
            Idle timeout in seconds.
        agent_id : str
            Owner agent id — stored in sandbox metadata.
        env : dict[str, str] | None
            Environment variables to set inside the sandbox.
        """
        self._ensure_sdk()
        from opensandbox import Sandbox

        conn = self._build_connection_config()
        sandbox = await Sandbox.create(
            image,
            entrypoint=["tail", "-f", "/dev/null"],
            env=env or {},
            timeout=timedelta(seconds=timeout),
            # Persist ownership server-side so /containers can map rows back
            # to agents (the control plane reads metadata["agent_id"]).
            metadata={"agent_id": agent_id, "name": name},
            connection_config=conn,
        )

        return SandboxInfo(
            sandbox_id=sandbox.id,
            agent_id=agent_id,
            image=image,
            status="running",
            created_at=datetime.now(timezone.utc),
            timeout_seconds=timeout,
            remaining_seconds=timeout,
            metadata={"name": name, "agent_id": agent_id},
        )

    async def destroy_sandbox(self, sandbox_id: str) -> None:
        """Kill a sandbox by id (server-level, works even when unreachable)."""
        self._ensure_sdk()
        mgr = await self._open_manager()
        try:
            await mgr.kill_sandbox(sandbox_id)
        finally:
            await mgr.close()

    async def renew_sandbox(self, sandbox_id: str, timeout: int) -> None:
        """Extend the sandbox timeout (expiration becomes now + *timeout*)."""
        self._ensure_sdk()
        mgr = await self._open_manager()
        try:
            await mgr.renew_sandbox(sandbox_id, timedelta(seconds=timeout))
        finally:
            await mgr.close()

    async def pause_sandbox(self, sandbox_id: str) -> None:
        """Pause a sandbox (preserve state, release compute)."""
        self._ensure_sdk()
        mgr = await self._open_manager()
        try:
            await mgr.pause_sandbox(sandbox_id)
        finally:
            await mgr.close()

    async def resume_sandbox(self, sandbox_id: str) -> None:
        """Resume a paused sandbox.

        Uses the SDK classmethod (server-side resume + endpoint re-resolution
        + readiness wait) — a plain ``Sandbox.connect()`` would fail while the
        container is still paused.
        """
        self._ensure_sdk()
        from opensandbox import Sandbox

        await Sandbox.resume(
            sandbox_id, connection_config=self._build_connection_config(),
        )

    async def list_sandboxes(self) -> list[SandboxInfo]:
        """List all sandboxes managed by the server."""
        self._ensure_sdk()
        from opensandbox import SandboxManager
        from opensandbox.models import SandboxFilter

        conn = self._build_connection_config()
        mgr = await SandboxManager.create(connection_config=conn)
        paged = await mgr.list_sandbox_infos(SandboxFilter())
        await mgr.close()
        result: list[SandboxInfo] = []
        for s in paged.sandbox_infos:
            metadata = getattr(s, "metadata", None) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            result.append(
                SandboxInfo(
                    sandbox_id=str(getattr(s, "id", None) or getattr(s, "sandbox_id", "")),
                    agent_id=str(metadata.get("agent_id", "") or ""),
                    image=_norm_image(getattr(s, "image", None)),
                    status=_norm_status(getattr(s, "status", None)),
                    created_at=_as_datetime(getattr(s, "created_at", None)),
                    expires_at=_as_datetime(getattr(s, "expires_at", None)),
                    platform=_norm_platform(getattr(s, "platform", None)),
                    metadata=metadata,
                )
            )
        return result

    async def get_metrics(self, sandbox_id: str) -> dict[str, Any]:
        """Fetch live CPU / memory metrics for one sandbox."""
        self._ensure_sdk()
        from opensandbox import Sandbox

        conn = self._build_connection_config()
        sb = await Sandbox.connect(
            sandbox_id, connection_config=conn, skip_health_check=True,
        )
        try:
            m = await sb.get_metrics()
            return {
                "cpu_count": getattr(m, "cpu_count", None),
                "cpu_used_percentage": getattr(m, "cpu_used_percentage", None),
                "memory_total_in_mib": getattr(m, "memory_total_in_mib", None),
                "memory_used_in_mib": getattr(m, "memory_used_in_mib", None),
            }
        finally:
            await sb.close()

    async def get_diagnostic_logs(self, sandbox_id: str) -> list[str]:
        """Fetch container-scoped diagnostic log lines for one sandbox."""
        self._ensure_sdk()
        from opensandbox import Sandbox

        conn = self._build_connection_config()
        sb = await Sandbox.connect(
            sandbox_id, connection_config=conn, skip_health_check=True,
        )
        try:
            diag = await sb.get_diagnostic_logs(scope="container")
            content = getattr(diag, "content", "") or ""
            return [line for line in content.splitlines() if line.strip()][-200:]
        finally:
            await sb.close()

    async def get_server_version(self) -> str:
        """Probe the OpenSandbox *server*'s advertised version.

        FastAPI servers expose their self-reported version in ``openapi.json``;
        this is the server build, not the locally installed SDK package.  Falls
        back to ``"unknown"`` when the endpoint is unavailable.
        """
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5) as hc:
                resp = await hc.get(f"{self._config.base_url}/openapi.json")
                resp.raise_for_status()
                info = resp.json().get("info") or {}
                version = info.get("version")
                return str(version) if version else "unknown"
        except Exception:
            return "unknown"
