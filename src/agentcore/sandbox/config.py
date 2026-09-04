"""Sandbox global configuration.

Reads from environment variables / .env file.  All fields have safe
defaults so the module can be imported even when sandbox is disabled.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SandboxGlobalConfig:
    """Global sandbox configuration, read from env vars or .env.

    Attributes
    ----------
    domain : str
        Hostname:port of the OpenSandbox server.
    protocol : str
        ``"http"`` or ``"https"``.
    api_key : str | None
        Optional API key for authentication.
    default_cleanup : str
        ``"on_exit"`` destroys all sandboxes when AgentCore shuts down;
        ``"never"`` leaves them running.
    enabled : bool
        Master switch — when ``False`` the sandbox module is inert.
    """

    domain: str = "localhost:8080"
    protocol: str = "http"
    api_key: str | None = None
    default_cleanup: str = "on_exit"
    enabled: bool = False

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> SandboxGlobalConfig:
        """Build config from environment variables.

        Recognised variables:

        - ``SANDBOX_ENABLED``  (``"true"`` / ``"false"``)
        - ``SANDBOX_DOMAIN``   (default ``localhost:8080``)
        - ``SANDBOX_PROTOCOL`` (default ``http``)
        - ``SANDBOX_API_KEY``  (optional)
        - ``SANDBOX_CLEANUP``  (``"on_exit"`` | ``"never"``)
        """
        e = env if env is not None else os.environ
        return cls(
            enabled=e.get("SANDBOX_ENABLED", "false").lower() in ("true", "1", "yes"),
            domain=e.get("SANDBOX_DOMAIN", "localhost:8080"),
            protocol=e.get("SANDBOX_PROTOCOL", "http"),
            api_key=e.get("SANDBOX_API_KEY") or None,
            default_cleanup=e.get("SANDBOX_CLEANUP", "on_exit"),
        )

    @property
    def base_url(self) -> str:
        """Full base URL for the OpenSandbox server."""
        return f"{self.protocol}://{self.domain}"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (api_key kept verbatim for storage)."""
        return {
            "enabled": self.enabled,
            "domain": self.domain,
            "protocol": self.protocol,
            "api_key": self.api_key or "",
            "default_cleanup": self.default_cleanup,
        }

    @classmethod
    def load_effective(cls) -> SandboxGlobalConfig:
        """Env defaults overridden by the persisted ``sandbox.json``.

        When the file is missing or unreadable, behaviour is identical to
        :meth:`from_env` (full backward compatibility).  When present, any
        recognised key overrides its env counterpart.
        """
        cfg = cls.from_env()
        raw = read_settings_file()
        if not raw:
            return cfg
        if "enabled" in raw:
            cfg.enabled = bool(raw["enabled"])
        if raw.get("domain"):
            cfg.domain = str(raw["domain"])
        if raw.get("protocol"):
            cfg.protocol = str(raw["protocol"])
        if "api_key" in raw:
            cfg.api_key = (raw["api_key"] or None)
        if raw.get("default_cleanup"):
            cfg.default_cleanup = str(raw["default_cleanup"])
        return cfg


# ---------------------------------------------------------------------------
# On-disk settings file (``.agentcore/sandbox.json``)
# ---------------------------------------------------------------------------


def _settings_path() -> Any:
    from agentcore.runtime import paths

    return paths.get_data_dir() / "sandbox.json"


def read_settings_file() -> dict[str, Any] | None:
    """Return the raw persisted settings dict, or ``None`` when absent."""
    path = _settings_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def write_settings_file(settings: dict[str, Any]) -> None:
    """Atomically persist the sandbox settings dict to ``sandbox.json``."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp", dir=str(path.parent), prefix=f".{path.stem}_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        if path.exists():
            path.unlink()
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class SandboxLifecycleConfig:
    """Per-agent sandbox lifecycle configuration.

    Stored inside ``settings["backend"]["lifecycle"]`` in agent.json.
    """

    strategy: str = "ephemeral"
    """One of ``"ephemeral"``, ``"pause-on-idle"``, ``"persistent"``."""

    timeout: int = 600
    """Sandbox idle timeout in seconds before the provider kills/pauses it."""

    renew_on_chat: bool = True
    """Automatically extend the timeout each time a chat message arrives."""

    renew_on_execute: bool = False
    """Extend the timeout each time the ``execute`` tool is called."""

    max_resume_count: int = 10
    """Strategy B: maximum number of resume cycles before forced rebuild."""

    idle_timeout: int = 300
    """Strategy B: seconds of inactivity before the sandbox is paused."""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SandboxLifecycleConfig:
        """Build from a dict (e.g. parsed from agent.json).

        A bare string (e.g. ``"lifecycle": "ephemeral"``) is accepted and
        treated as the strategy name — legacy configs used this shorthand.
        """
        if not data:
            return cls()
        if isinstance(data, str):
            return cls(strategy=data)
        return cls(
            strategy=data.get("strategy", "ephemeral"),
            timeout=int(data.get("timeout", 600)),
            renew_on_chat=data.get("renew_on_chat", True),
            renew_on_execute=data.get("renew_on_execute", False),
            max_resume_count=int(data.get("max_resume_count", 10)),
            idle_timeout=int(data.get("idle_timeout", 300)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "timeout": self.timeout,
            "renew_on_chat": self.renew_on_chat,
            "renew_on_execute": self.renew_on_execute,
            "max_resume_count": self.max_resume_count,
            "idle_timeout": self.idle_timeout,
        }
