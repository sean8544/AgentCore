"""Agent factory: maps an ``agent.json`` configuration to deepagents agents.

This module bridges AgentCore's per-agent configuration (the workspace's
``agent.json`` file) with the deepagents SDK runtime.  The central class
:class:`AgentFactory` translates model, tool, permission, HITL, backend,
and prompt settings from a plain configuration dict into keyword arguments
for :func:`deepagents.create_deep_agent`.

Configuration shape (see :data:`~agentcore.runtime.workspace.DEFAULT_AGENT_JSON`)::

    {
        "model": {"provider": ..., "name": ..., "base_url": ..., "api_key_env": ...},
        "tools": {"enabled": [...], "disabled": [...]},
        "settings": {"backend": {...}, "permissions": [...], "interrupt_rules": [...]}
    }
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import FilesystemBackend, StateBackend
from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langgraph.graph.state import CompiledStateGraph

from agentcore.constants import BUILT_IN_TOOLS
from agentcore.runtime.model_factory import resolve_model
from agentcore.runtime.workspace import KERNEL_FILE_NAMES, SKILLS_DIR_NAME

# SDK tool exclusion — the same middleware HarnessProfile.excluded_tools
# feeds inside create_deep_agent.  Per-agent exclusion cannot be expressed
# through the HarnessProfile registry (keys are model-keyed and
# register_harness_profile only merges additively), so the middleware is
# injected directly via create_deep_agent(middleware=[...]).
try:
    from deepagents.middleware._tool_exclusion import (
        _ToolExclusionMiddleware as _SdkToolExclusionMiddleware,
    )
except ImportError:  # pragma: no cover - depends on SDK internals
    _SdkToolExclusionMiddleware = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SDK version used for tool-surface gating.
# ---------------------------------------------------------------------------
_DEEPAGENTS_VERSION = "0.7.5"


# ===================================================================
# Tool-surface policy (migrated from the removed policies.py module)
# ===================================================================

class BackendType(str, Enum):
    """Supported runtime backend categories."""

    LOCAL = "local"
    SANDBOX = "sandbox"


@dataclass(frozen=True)
class ToolPolicy:
    """Policy intent for a built-in harness tool."""

    tool_name: str
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.tool_name not in BUILT_IN_TOOLS:
            msg = f"Unknown built-in tool: {self.tool_name}"
            raise ValueError(msg)


@dataclass(frozen=True)
class ToolSurfaceResult:
    """Effective tool surface after policy and capability checks."""

    allowed_tools: tuple[str, ...]
    unavailable_tools: tuple[str, ...]


def _parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a semver-like version string into a tuple."""

    clean = version.strip().split("+")[0].split("-")[0]
    parts = clean.split(".")
    if len(parts) < 3:
        parts += ["0"] * (3 - len(parts))
    try:
        major, minor, patch = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError as exc:
        msg = f"Invalid deepagents version: {version!r}"
        raise ValueError(msg) from exc
    return major, minor, patch


def backend_supported_tools(
    backend: BackendType,
    *,
    deepagents_version: str,
) -> tuple[str, ...]:
    """Return tools technically supported by backend and deepagents version."""

    supported = set(BUILT_IN_TOOLS)

    if backend != BackendType.SANDBOX:
        supported.discard("execute")

    if _parse_semver(deepagents_version) < (0, 7, 0):
        supported.discard("delete")

    return tuple(sorted(supported))


def compute_effective_tool_surface(
    policies: Iterable[ToolPolicy],
    *,
    backend: BackendType,
    deepagents_version: str,
) -> ToolSurfaceResult:
    """Compute effective tool set from policy intent and runtime capability."""

    policy_map = {policy.tool_name: policy.enabled for policy in policies}
    for tool in BUILT_IN_TOOLS:
        policy_map.setdefault(tool, True)

    supported = set(backend_supported_tools(backend, deepagents_version=deepagents_version))

    allowed = tuple(
        sorted(
            tool
            for tool in BUILT_IN_TOOLS
            if policy_map[tool] and tool in supported
        )
    )
    unavailable = tuple(
        sorted(tool for tool in BUILT_IN_TOOLS if policy_map[tool] and tool not in supported)
    )

    return ToolSurfaceResult(allowed_tools=allowed, unavailable_tools=unavailable)


# ===================================================================
# Helpers
# ===================================================================

def _create_backend(
    settings: dict[str, Any],
    workspace_dir: Path | None = None,
) -> BackendProtocol:
    """Instantiate the appropriate deepagents backend from config settings.

    Sandbox policy (hardened):

    1. *workspace_dir* — when provided, a :class:`FilesystemBackend`
       rooted at the agent's workspace directory is used so files
       written by the built-in tools land inside
       ``.agentcore/workspace/agent/{agent_id}/`` and cannot escape the
       workspace.  Custom ``settings["backend"]["root_dir"]`` overrides
       are **not** honoured.
    2. ``settings["backend"]["type"]`` — ``"local"`` | ``"sandbox"``
       (default ``"local"``); without a workspace the sandbox type
       degrades to an ephemeral :class:`StateBackend`.
    3. Fallback — ephemeral :class:`StateBackend`.

    :class:`FilesystemBackend` is always created with
    ``virtual_mode=True`` so paths cannot escape the root via ``..``.
    """
    if workspace_dir is not None:
        root_path = Path(workspace_dir)
        root_path.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Creating FilesystemBackend(root_dir=%s, virtual_mode=True) "
            "bound to workspace",
            root_path,
        )
        return FilesystemBackend(root_dir=root_path, virtual_mode=True)

    backend_cfg = settings.get("backend", {})
    backend_type = backend_cfg.get("type", BackendType.LOCAL)

    if isinstance(backend_type, str):
        try:
            backend_type = BackendType(backend_type)
        except ValueError:
            logger.warning("Unknown backend type %r; falling back to local", backend_type)
            backend_type = BackendType.LOCAL

    if backend_type == BackendType.SANDBOX:
        # SandboxBackend requires additional infra; fall back to StateBackend
        # (ephemeral, in-graph storage) which is always available.
        logger.info("Sandbox backend requested — using StateBackend (ephemeral)")
        return StateBackend()

    # No workspace_dir — custom root_dir bypass is intentionally NOT
    # supported; the only safe fallback is the ephemeral StateBackend.
    if backend_cfg.get("root_dir"):
        logger.warning(
            "settings.backend.root_dir is ignored for sandbox hardening — "
            "using StateBackend (ephemeral)"
        )
    logger.info("No workspace available — using StateBackend (ephemeral)")
    return StateBackend()


def _map_permissions(perm_dicts: list[dict[str, Any]]) -> list[FilesystemPermission]:
    """Convert permission rule dicts to deepagents FilesystemPermission.

    Each dict is expected to follow the shape of the (removed)
    ``PermissionRule`` model:

    - ``operations``: iterable of ``"read"`` / ``"write"``
    - ``paths``: iterable of glob patterns starting with ``/``
    - ``mode``: ``"allow"`` | ``"deny"``
    """
    result: list[FilesystemPermission] = []
    for rule in perm_dicts:
        try:
            operations = list(rule.get("operations", []))
            paths = list(rule.get("paths", []))
            mode = rule.get("mode", "allow")
            if not paths:
                continue
            result.append(
                FilesystemPermission(
                    operations=operations,
                    paths=paths,
                    mode=mode,
                )
            )
        except (ValueError, TypeError) as exc:
            logger.warning("Skipping invalid permission rule %r: %s", rule, exc)
    return result


def _map_interrupt_on(rules: list[dict[str, Any]]) -> dict[str, bool] | None:
    """Convert interrupt rule dicts to an ``interrupt_on`` mapping.

    Each dict follows the (removed) ``InterruptRule`` shape:

    - ``tool_name``: name of the tool
    - ``require_approval``: whether to interrupt
    """
    interrupt_on: dict[str, bool] = {}
    for rule in rules:
        tool_name = rule.get("tool_name", "")
        if rule.get("require_approval", False) and tool_name:
            interrupt_on[tool_name] = True
    return interrupt_on if interrupt_on else None


def _compute_excluded_tools(
    tools_cfg: dict[str, Any],
    backend_type: BackendType,
) -> frozenset[str]:
    """Determine which built-in tools must be excluded.

    ``tools_cfg`` is the ``agent.json`` ``tools`` section:

    - ``disabled``: explicit deny-list — always excluded.
    - ``enabled``: when non-empty, acts as a whitelist — every built-in
      tool not listed is excluded.
    - ``execute`` is always excluded on non-sandbox backends regardless
      of configuration (sandbox hardening).

    The result is further intersected with backend capability
    (:func:`compute_effective_tool_surface`) so tools the backend cannot
    run anyway are reported consistently.
    """
    disabled = set(tools_cfg.get("disabled", []) or [])
    enabled = list(tools_cfg.get("enabled", []) or [])

    policies: list[ToolPolicy] = []
    for tool in BUILT_IN_TOOLS:
        if enabled:
            policies.append(ToolPolicy(tool_name=tool, enabled=tool in enabled))
        else:
            policies.append(ToolPolicy(tool_name=tool, enabled=tool not in disabled))

    surface = compute_effective_tool_surface(
        policies,
        backend=backend_type,
        deepagents_version=_DEEPAGENTS_VERSION,
    )
    allowed_set = set(surface.allowed_tools)
    excluded = frozenset(t for t in BUILT_IN_TOOLS if t not in allowed_set)
    # Sandbox hardening: ``execute`` never reaches a non-sandbox backend,
    # even if the configuration tried to enable it explicitly.
    if backend_type != BackendType.SANDBOX:
        excluded = excluded | frozenset({"execute"})
    return excluded


def _resolve_backend_type(settings: dict[str, Any]) -> BackendType:
    """Parse ``settings["backend"]["type"]`` defensively."""
    backend_cfg = settings.get("backend", {})
    backend_type_str = backend_cfg.get("type", BackendType.LOCAL)
    try:
        return BackendType(backend_type_str)
    except ValueError:
        return BackendType.LOCAL


# ===================================================================
# AgentFactory
# ===================================================================

class AgentFactory:
    """Translate an ``agent.json`` configuration into a running deep agent.

    The factory is intentionally stateless — each call to :meth:`create_agent`
    produces an independent agent graph.  This makes it safe to share a single
    ``AgentFactory`` across threads / requests.

    Typical usage::

        factory = AgentFactory()
        agent = factory.create_agent(workspace.read_agent_config())
        result = agent.invoke({"messages": "Hello"})
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_agent(
        self,
        agent_config: dict[str, Any],
        workspace_dir: Path | None = None,
        workspace: Any = None,
        subagents: list[Any] | None = None,
        **kwargs: Any,
    ) -> CompiledStateGraph:
        """Create a deepagents agent from an ``agent.json`` configuration.

        Mapping summary
        ---------------
        | Config field                    | ``create_deep_agent`` param    |
        |---------------------------------|--------------------------------|
        | ``agent_config["model"]``       | ``model``                      |
        | ``agent_config["tools"]``       | SDK ``_ToolExclusionMiddleware``|
        | ``settings["permissions"]``     | ``permissions``                |
        | ``settings["interrupt_rules"]`` | ``interrupt_on``               |
        | ``settings["backend"]``         | ``backend``                    |

        Parameters
        ----------
        agent_config:
            The agent configuration dict (as read from the workspace's
            ``agent.json`` via
            :meth:`~agentcore.runtime.workspace.Workspace.read_agent_config`).
        workspace_dir:
            Optional workspace directory (``.agentcore/workspace/agent/{id}/``).
            When provided, the file backend is bound to it so the agent's
            built-in file tools operate inside the workspace.
        workspace:
            Optional :class:`~agentcore.runtime.workspace.Workspace`
            instance.  When provided, the SDK's built-in ``memory`` /
            ``skills`` mechanisms are wired automatically: the workspace
            kernel files (``bootstrap.md`` / ``agent.md`` / ``profile.md``
            / ``soul.md``, when present)
            are injected via ``memory=[]`` (paths relative to the backend
            root) and the ``skills/`` directory is exposed via
            ``skills=[]``.  Explicit ``memory`` / ``skills`` entries in
            ``**kwargs`` take precedence.
        subagents:
            Optional list of deepagents ``SubAgent`` / ``CompiledSubAgent``
            specs injected as ``create_deep_agent(subagents=...)`` so the
            agent can delegate to other agents through the SDK's ``task``
            tool.  An explicit ``subagents`` entry in ``**kwargs`` takes
            precedence.
        **kwargs:
            Extra keyword arguments forwarded directly to
            :func:`deepagents.create_deep_agent`.  Use these to supply
            values not captured in the configuration (e.g. ``checkpointer``,
            ``store``, ``subagents``, ``skills``, ``memory``).

        Returns
        -------
        CompiledStateGraph
            A configured, ready-to-invoke deep agent.

        Raises
        ------
        ValueError
            If the model configuration is invalid.
        """
        settings = agent_config.get("settings", {}) or {}

        # --- SDK memory / skills from the workspace -----------------
        # Kernel files are loaded from disk at agent startup and appended
        # to the system prompt inside an ``<agent_memory>`` block by the
        # SDK's MemoryMiddleware; the ``skills/`` directory is scanned by
        # the SkillsMiddleware.  Paths are relative to the backend root
        # (the workspace directory).
        if workspace is not None:
            if kwargs.get("memory") is None:
                kernel_paths = [
                    f"/{name}"
                    for name in KERNEL_FILE_NAMES
                    if (workspace.workspace_dir / name).exists()
                ]
                if kernel_paths:
                    kwargs["memory"] = kernel_paths
            if kwargs.get("skills") is None:
                skills_dir = workspace.workspace_dir / SKILLS_DIR_NAME
                if skills_dir.exists() and skills_dir.is_dir():
                    kwargs["skills"] = [f"/{SKILLS_DIR_NAME}/"]

        # --- Model ---------------------------------------------------
        model_cfg = agent_config.get("model")
        model = resolve_model(model_cfg)
        if model is not None:
            logger.info("Model: %s", model if isinstance(model, str) else repr(model))
        else:
            logger.warning(
                "No model configured in agent config — deepagents will use "
                "its deprecated default model",
            )

        # --- Backend -------------------------------------------------
        backend = _create_backend(settings, workspace_dir)

        # --- Permissions ---------------------------------------------
        perm_cfgs = settings.get("permissions", [])
        permissions = _map_permissions(perm_cfgs) if perm_cfgs else None
        if permissions:
            logger.info("Mapped %d permission rule(s)", len(permissions))

        # --- Interrupt-on (HITL) ------------------------------------
        interrupt_rules = settings.get("interrupt_rules", [])
        interrupt_on = _map_interrupt_on(interrupt_rules)
        if interrupt_on:
            logger.info("HITL interrupt_on for tools: %s", list(interrupt_on.keys()))

        # --- Tool exclusion middleware -------------------------------
        backend_type = _resolve_backend_type(settings)
        excluded = _compute_excluded_tools(
            agent_config.get("tools", {}) or {}, backend_type
        )
        extra_middleware: list[AgentMiddleware[Any, Any, Any]] = list(
            kwargs.pop("middleware", []) or []
        )
        if excluded:
            if _SdkToolExclusionMiddleware is not None:
                logger.info(
                    "Excluding disabled built-in tools via SDK "
                    "tool-exclusion middleware: %s",
                    sorted(excluded),
                )
                extra_middleware.append(
                    _SdkToolExclusionMiddleware(excluded=excluded)
                )
            else:  # pragma: no cover - SDK internals changed
                logger.warning(
                    "deepagents _ToolExclusionMiddleware unavailable — "
                    "tool exclusion NOT applied (wanted: %s)",
                    sorted(excluded),
                )

        # --- Custom tools (additive) ---------------------------------
        custom_tools = settings.get("custom_tools") or kwargs.pop("tools", None)

        # --- MCP tools (optional, from workspace mcp.json) -----------
        # Enabled MCP servers are probed and their LangChain tools merged
        # into the tool list.  This is entirely best-effort: missing
        # packages, dead servers or timeouts degrade to an empty tool
        # list and never block agent creation.
        if workspace is not None:
            try:
                mcp_config = workspace.read_mcp_config()
            except Exception:  # noqa: BLE001 — MCP must never break creation
                logger.warning("Failed to read mcp.json — MCP tools skipped")
                mcp_config = {}
            if any(
                isinstance(cfg, dict) and cfg.get("enabled", True)
                for cfg in mcp_config.values()
            ):
                from agentcore.runtime.mcp_client import load_mcp_tools_blocking

                mcp_tools = load_mcp_tools_blocking(mcp_config)
                if mcp_tools:
                    custom_tools = list(custom_tools or []) + list(mcp_tools)

        # --- Subagents (opt-in cross-agent delegation) ---------------
        if subagents and kwargs.get("subagents") is None:
            kwargs["subagents"] = subagents

        # --- Assemble & create ---------------------------------------
        create_kwargs: dict[str, Any] = {
            "system_prompt": settings.get("system_prompt") or None,
            "permissions": permissions,
            "backend": backend,
            "interrupt_on": interrupt_on,
        }

        if model is not None:
            create_kwargs["model"] = model

        if custom_tools:
            create_kwargs["tools"] = custom_tools

        if extra_middleware:
            create_kwargs["middleware"] = extra_middleware

        # Caller-supplied kwargs override everything derived from the config.
        create_kwargs.update(kwargs)

        logger.info("Calling create_deep_agent with keys: %s", sorted(create_kwargs))
        agent = create_deep_agent(**create_kwargs)

        logger.info("Agent created successfully from agent.json config")
        return agent
