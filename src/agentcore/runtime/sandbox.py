"""Sandbox backend abstraction for isolated code execution (Tasks 3.6, 3.7).

Provides a :class:`SandboxBackendFactory` interface that creates isolated
execution environments.  The default implementation attempts to connect to
the SDK's built-in ``SandboxBackend`` (E2B-compatible); when no sandbox
infrastructure is available, it raises a clear error rather than silently
falling back to host execution.

Design decisions
----------------
* **No silent fallback** — if a sandbox is configured but unavailable, the
  agent creation must fail loudly (aligned with the spec's "sandbox
  unavailable → explicit error" requirement).
* **Provider-agnostic** — the factory interface accepts a provider name
  (``"e2b"`` or ``"daytona"``) so alternative backends can be plugged in.
* **Lazy initialisation** — sandbox connections are only created when an
  agent actually needs one, not at application startup.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class SandboxUnavailableError(RuntimeError):
    """Raised when a sandbox backend is configured but cannot be reached.

    Callers (specifically :class:`~agentcore.runtime.agent_factory.AgentFactory`)
    should propagate this error to the user rather than falling back to
    host execution.
    """


class SandboxBackendFactory(ABC):
    """Interface for creating sandbox backend instances.

    Implementations connect to a specific sandbox provider (E2B, Daytona,
    etc.) and return a ``BackendProtocol``-compatible object that the
    deepagents SDK can use for file operations and code execution.
    """

    @abstractmethod
    def create(self, **kwargs: Any) -> Any:
        """Create and return a sandbox backend instance.

        Raises
        ------
        SandboxUnavailableError
            If the sandbox provider cannot be reached or is not configured.
        """


class E2BSandboxFactory(SandboxBackendFactory):
    """Sandbox factory that attempts to use the SDK's E2B-compatible sandbox.

    The deepagents SDK ships a ``SandboxBackend`` abstract class in
    ``deepagents.backends.sandbox``.  Concrete E2B integration requires
    an API key and network access.  When these are not available, a
    :class:`SandboxUnavailableError` is raised.
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = api_key

    def create(self, **kwargs: Any) -> Any:
        """Attempt to create an E2B sandbox backend.

        Raises :class:`SandboxUnavailableError` if the SDK sandbox
        infrastructure is not available or the API key is missing.
        """
        import os

        api_key = self._api_key or os.environ.get("E2B_API_KEY")
        if not api_key:
            raise SandboxUnavailableError(
                "Sandbox backend requested but E2B_API_KEY is not set. "
                "Set the environment variable or provide an api_key to the "
                "sandbox factory.  The agent will not execute code until "
                "a sandbox is available."
            )

        try:
            # The SDK's SandboxBackend is abstract — concrete E2B
            # integration requires a subclass that implements execute()
            # and upload_files().  For now, we verify the SDK has the
            # sandbox infrastructure and raise if it doesn't.
            from deepagents.backends.sandbox import SandboxBackend  # noqa: F401

            logger.info(
                "E2B sandbox factory ready (API key present). "
                "Concrete sandbox execution requires a SandboxBackend "
                "subclass with execute() implementation."
            )
            # Return None to signal that the factory is ready but no
            # concrete implementation exists yet — the agent_factory
            # will handle this by raising SandboxUnavailableError.
            raise SandboxUnavailableError(
                "E2B sandbox infrastructure detected but no concrete "
                "implementation is configured.  A SandboxBackend subclass "
                "with execute() and upload_files() is required.  "
                "See docs for sandbox setup instructions."
            )
        except ImportError:
            raise SandboxUnavailableError(
                "Sandbox backend requested but the deepagents SDK sandbox "
                "module is not available.  Please upgrade deepagents or "
                "configure a different sandbox provider."
            )


class DaytonaSandboxFactory(SandboxBackendFactory):
    """Placeholder for Daytona sandbox integration.

    Daytona integration follows the same interface as E2B but uses
    Daytona's API for workspace management.
    """

    def __init__(self, *, api_key: str | None = None, endpoint: str | None = None) -> None:
        self._api_key = api_key
        self._endpoint = endpoint

    def create(self, **kwargs: Any) -> Any:
        raise SandboxUnavailableError(
            "Daytona sandbox integration is not yet implemented. "
            "Use E2B or configure a local backend instead."
        )


# ---------------------------------------------------------------------------
# Factory registry
# ---------------------------------------------------------------------------

_FACTORIES: dict[str, type[SandboxBackendFactory]] = {
    "e2b": E2BSandboxFactory,
    "daytona": DaytonaSandboxFactory,
}


def get_sandbox_factory(provider: str) -> SandboxBackendFactory:
    """Return a :class:`SandboxBackendFactory` for the given provider.

    Raises :class:`ValueError` for unknown providers.
    """
    cls = _FACTORIES.get(provider.lower())
    if cls is None:
        raise ValueError(
            f"Unknown sandbox provider {provider!r}. "
            f"Available: {sorted(_FACTORIES)}"
        )
    return cls()
