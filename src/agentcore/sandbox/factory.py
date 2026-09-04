"""Sandbox factories — bridges to the runtime sandbox interface.

Implements :class:`agentcore.runtime.sandbox.SandboxBackendFactory` so
that ``get_sandbox_factory("opensandbox")`` or ``get_sandbox_factory("aio")``
returns a working factory.

OpenSandbox factory
~~~~~~~~~~~~~~~~~~~
The factory creates an :class:`opensandbox.SandboxSync` instance and wraps
it with :class:`langchain_opensandbox.OpenSandboxBackend`, which inherits
from ``deepagents.backends.sandbox.BaseSandbox`` and implements the three
primitives (``execute``, ``upload_files``, ``download_files``) required by
the deepagents backend protocol.

AIO factory
~~~~~~~~~~~
Uses the same OpenSandbox infrastructure to create a container from the
AIO image (``ghcr.io/agent-infra/sandbox``), then wraps it with
:class:`AIOSandboxBackend` which talks to the AIO server via the
``agent_sandbox`` SDK.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from agentcore.runtime.sandbox import (
    SandboxBackendFactory,
    SandboxUnavailableError,
)
from agentcore.sandbox.config import SandboxGlobalConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenSandbox Factory
# ---------------------------------------------------------------------------


class OpenSandboxFactory(SandboxBackendFactory):
    """Factory that creates OpenSandbox-backed sandbox backends.

    Uses :class:`opensandbox.SandboxSync` to create a container, then wraps
    it with :class:`langchain_opensandbox.OpenSandboxBackend` (which inherits
    from ``deepagents.backends.sandbox.BaseSandbox``).
    """

    def __init__(self, config: SandboxGlobalConfig | None = None) -> None:
        self._config = config or SandboxGlobalConfig.from_env()

    def _build_backend(
        self,
        agent_id: str,
        image: str,
        timeout: int,
    ) -> Any:
        """Create a SandboxSync container and wrap it with OpenSandboxBackend."""
        from langchain_opensandbox import OpenSandboxBackend
        from opensandbox import SandboxSync
        from opensandbox.config import ConnectionConfigSync

        logger.info(
            "Creating OpenSandbox backend for agent %s (image=%s, timeout=%ds)",
            agent_id, image, timeout,
        )

        conn_config = ConnectionConfigSync(
            domain=self._config.domain,
            protocol=self._config.protocol,
            api_key=self._config.api_key,
        )
        sandbox = SandboxSync.create(
            image=image,
            timeout=timedelta(seconds=timeout),
            connection_config=conn_config,
            # Server-side ownership tag — the control plane's container list
            # maps rows back to agents via metadata["agent_id"].
            metadata={"agent_id": agent_id},
        )
        return OpenSandboxBackend(sandbox=sandbox, timeout=timeout)

    def create(self, **kwargs: Any) -> Any:
        """Create a sandbox backend for the given agent.

        Keyword Arguments
        -----------------
        agent_id : str
            The agent that owns this sandbox.
        image : str
            Docker image (default ``python:3.12-slim``).
        timeout : int
            Idle timeout in seconds.
        """
        if not self._config.enabled:
            raise SandboxUnavailableError(
                "Sandbox is not enabled. Set SANDBOX_ENABLED=true or "
                "configure backend.lifecycle.enabled in the agent settings."
            )

        agent_id = kwargs.get("agent_id", "unknown")
        image = kwargs.get("image", "python:3.12-slim")
        timeout = int(kwargs.get("timeout", 600))

        try:
            return self._build_backend(agent_id, image, timeout)
        except ImportError as exc:
            raise SandboxUnavailableError(
                f"langchain-sandbox-opensandbox not installed: {exc}. "
                "Install with: pip install langchain-sandbox-opensandbox"
            ) from exc
        except SandboxUnavailableError:
            raise
        except Exception as exc:
            raise SandboxUnavailableError(
                f"Failed to create OpenSandbox sandbox: {exc}. "
                "Check that the OpenSandbox server is running at "
                f"{self._config.base_url}"
            ) from exc

    async def create_async(self, **kwargs: Any) -> Any:
        """Async version of :meth:`create` for use inside event loops."""
        import asyncio

        if not self._config.enabled:
            raise SandboxUnavailableError(
                "Sandbox is not enabled. Set SANDBOX_ENABLED=true."
            )

        agent_id = kwargs.get("agent_id", "unknown")
        image = kwargs.get("image", "python:3.12-slim")
        timeout = int(kwargs.get("timeout", 600))

        try:
            return await asyncio.to_thread(
                self._build_backend, agent_id, image, timeout,
            )
        except ImportError as exc:
            raise SandboxUnavailableError(
                f"langchain-sandbox-opensandbox not installed: {exc}. "
                "Install with: pip install langchain-sandbox-opensandbox"
            ) from exc
        except Exception as exc:
            raise SandboxUnavailableError(
                f"Failed to create OpenSandbox sandbox: {exc}. "
                "Check that the OpenSandbox server is running at "
                f"{self._config.base_url}"
            ) from exc


# ---------------------------------------------------------------------------
# AIO (All-in-One) Sandbox Factory
# ---------------------------------------------------------------------------

# Default AIO image — single container with browser, shell, file system,
# VSCode, Jupyter and MCP server exposed on internal port 8080.
AIO_DEFAULT_IMAGE = "ghcr.io/agent-infra/sandbox:latest"

# The AIO image's own entrypoint boots the all-in-one HTTP server on 8080
# (plus the sandbox server / browser the health check probes). OpenSandbox
# replaces the image ENTRYPOINT with its execd bootstrap, so this must be
# passed explicitly — otherwise only execd starts and every shell/file
# request to port 8080 fails with 502 Bad Gateway.
AIO_ENTRYPOINT = ["/opt/gem/run.sh"]


class AIOSandboxFactory(SandboxBackendFactory):
    """Factory that creates AIO-backed sandbox backends.

    Uses :class:`opensandbox.SandboxSync` to create a container from the
    AIO image, then obtains the HTTP endpoint for port 8080 and constructs
    an :class:`AIOSandboxBackend` that talks to the AIO server via the
    ``agent_sandbox`` SDK.
    """

    def __init__(self, config: SandboxGlobalConfig | None = None) -> None:
        self._config = config or SandboxGlobalConfig.from_env()

    def _build_backend(
        self,
        agent_id: str,
        image: str,
        timeout: int,
    ) -> Any:
        """Create an AIO container and wrap it with AIOSandboxBackend."""
        from opensandbox import SandboxSync
        from opensandbox.config import ConnectionConfigSync

        from agentcore.sandbox.aio_sandbox import AIOSandboxBackend

        logger.info(
            "Creating AIO backend for agent %s (image=%s, timeout=%ds)",
            agent_id, image, timeout,
        )

        conn_config = ConnectionConfigSync(
            domain=self._config.domain,
            protocol=self._config.protocol,
            api_key=self._config.api_key,
        )
        sandbox = SandboxSync.create(
            image=image,
            timeout=timedelta(seconds=timeout),
            connection_config=conn_config,
            entrypoint=list(AIO_ENTRYPOINT),
            # Server-side ownership tag — see the OpenSandbox factory above.
            metadata={"agent_id": agent_id},
        )

        # Get the HTTP endpoint for the AIO server (port 8080)
        endpoint = sandbox.get_endpoint(8080)
        base_url: str = endpoint.endpoint
        # Ensure the URL has a protocol (OpenSandbox SDK may return just host:port)
        if base_url and not base_url.startswith(('http://', 'https://')):
            base_url = f'http://{base_url}'
        headers: dict[str, str] = dict(endpoint.headers) if endpoint.headers else {}

        return AIOSandboxBackend(
            sandbox_id=sandbox.id,
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )

    def create(self, **kwargs: Any) -> Any:
        """Create an AIO sandbox backend.

        Keyword Arguments
        -----------------
        agent_id : str
            The agent that owns this sandbox.
        image : str
            Docker image (default ``ghcr.io/agent-infra/sandbox:latest``).
        timeout : int
            Idle timeout in seconds.
        """
        if not self._config.enabled:
            raise SandboxUnavailableError(
                "Sandbox is not enabled. Set SANDBOX_ENABLED=true or "
                "configure backend.lifecycle.enabled in the agent settings."
            )

        agent_id = kwargs.get("agent_id", "unknown")
        image = kwargs.get("image", AIO_DEFAULT_IMAGE)
        timeout = int(kwargs.get("timeout", 600))

        try:
            return self._build_backend(agent_id, image, timeout)
        except ImportError as exc:
            raise SandboxUnavailableError(
                f"Required package not installed: {exc}. "
                "Install with: pip install opensandbox agent-sandbox"
            ) from exc
        except SandboxUnavailableError:
            raise
        except Exception as exc:
            raise SandboxUnavailableError(
                f"Failed to create AIO sandbox: {exc}. "
                "Check that the OpenSandbox server is running at "
                f"{self._config.base_url}"
            ) from exc

    async def create_async(self, **kwargs: Any) -> Any:
        """Async version of :meth:`create` for use inside event loops."""
        import asyncio

        if not self._config.enabled:
            raise SandboxUnavailableError(
                "Sandbox is not enabled. Set SANDBOX_ENABLED=true."
            )

        agent_id = kwargs.get("agent_id", "unknown")
        image = kwargs.get("image", AIO_DEFAULT_IMAGE)
        timeout = int(kwargs.get("timeout", 600))

        try:
            return await asyncio.to_thread(
                self._build_backend, agent_id, image, timeout,
            )
        except ImportError as exc:
            raise SandboxUnavailableError(
                f"Required package not installed: {exc}. "
                "Install with: pip install opensandbox agent-sandbox"
            ) from exc
        except Exception as exc:
            raise SandboxUnavailableError(
                f"Failed to create AIO sandbox: {exc}. "
                "Check that the OpenSandbox server is running at "
                f"{self._config.base_url}"
            ) from exc
