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
from pydantic import BaseModel, Field

from agentcore.constants import BUILT_IN_TOOLS
from agentcore.runtime.model_factory import resolve_model
from agentcore.runtime.workspace import (
    ALL_KERNEL_FILE_NAMES,
    MEMORY_DIR_NAME,
    SKILLS_DIR_NAME,
)


# ---------------------------------------------------------------------------
# Public tool-exclusion middleware (replaces private SDK import)
#
# Uses only public SDK types: ``AgentMiddleware`` and ``ModelRequest.override``.
# Per-agent exclusion cannot be expressed through the HarnessProfile registry
# (keys are model-keyed and register_harness_profile only merges additively),
# so the middleware is injected directly via create_deep_agent(middleware=[...]).
# ---------------------------------------------------------------------------


class _ToolExclusionMiddleware(AgentMiddleware[Any, Any, Any]):
    """Filter excluded built-in tools from the model request.

    Placed after tool-injecting middleware so it can strip both
    user-supplied and SDK-injected tools from the visible tool set
    before the model sees them.

    Uses only public SDK API: ``wrap_model_call`` /
    ``awrap_model_call`` and ``ModelRequest.override(tools=...)``.
    """

    def __init__(self, *, excluded: frozenset[str]) -> None:
        self._excluded = excluded

    @staticmethod
    def _tool_name(tool: Any) -> str | None:
        if isinstance(tool, dict):
            name = tool.get("name")
            return name if isinstance(name, str) else None
        return getattr(tool, "name", None)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        if self._excluded:
            filtered = [
                t for t in request.tools
                if self._tool_name(t) not in self._excluded
            ]
            request = request.override(tools=filtered)
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        if self._excluded:
            filtered = [
                t for t in request.tools
                if self._tool_name(t) not in self._excluded
            ]
            request = request.override(tools=filtered)
        return await handler(request)


class _SkillExclusionMiddleware(AgentMiddleware[Any, Any, Any]):
    """Filter disabled workspace skills out of the loaded skill index.

    The SDK's ``SkillsMiddleware`` (first in the base stack) scans the
    whole workspace ``skills/`` directory into ``state['skills_metadata']``
    and renders that list into the system prompt.  This middleware runs
    later in the graph's ``before_agent`` chain, so its state update
    overwrites the loaded index with only the enabled skills before the
    model ever sees them — implementing per-agent skill toggling
    (``agent.json`` ``skills.disabled``) without touching the SDK.
    """

    def __init__(self, *, disabled: frozenset[str]) -> None:
        self._disabled = disabled

    def _filtered_update(self, state: Any) -> dict[str, Any] | None:
        metadata = state.get("skills_metadata")
        if not isinstance(metadata, list):
            return None
        filtered = [
            skill for skill in metadata
            if skill.get("name") not in self._disabled
        ]
        if len(filtered) == len(metadata):
            return None
        return {"skills_metadata": filtered}

    def before_agent(self, state: Any, runtime: Any, config: Any) -> dict[str, Any] | None:  # noqa: ARG002
        return self._filtered_update(state)

    async def abefore_agent(self, state: Any, runtime: Any, config: Any) -> dict[str, Any] | None:  # noqa: ARG002
        return self._filtered_update(state)


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

    Sandbox priority: when ``settings["backend"]["type"]`` is
    ``"sandbox"``, the sandbox backend takes precedence over the
    workspace FilesystemBackend — even when *workspace_dir* is set.
    """
    # --- Sandbox check FIRST (overrides workspace_dir) ---
    backend_cfg = settings.get("backend", {})
    backend_type = backend_cfg.get("type", BackendType.LOCAL)

    if isinstance(backend_type, str):
        try:
            backend_type = BackendType(backend_type)
        except ValueError:
            logger.warning("Unknown backend type %r; falling back to local", backend_type)
            backend_type = BackendType.LOCAL

    if backend_type == BackendType.SANDBOX:
        # Task 3.6/3.7: Sandbox backend must be explicitly available;
        # no silent fallback to host execution.
        from agentcore.runtime.sandbox import SandboxUnavailableError, get_sandbox_factory

        provider = backend_cfg.get("provider", "opensandbox")
        try:
            factory = get_sandbox_factory(provider)
            backend_instance = factory.create()
            if backend_instance is not None:
                logger.info("Sandbox backend created via %s provider", provider)
                return backend_instance
        except SandboxUnavailableError:
            # Re-raise — the spec requires explicit failure, not silent
            # fallback.
            raise
        except Exception as exc:
            from agentcore.runtime.sandbox import SandboxUnavailableError as SUE
            raise SUE(
                f"Sandbox backend ({provider}) failed to initialise: {exc}. "
                "The agent will not execute code until a sandbox is available."
            ) from exc

    # --- Local backend: use workspace_dir if available ---
    if workspace_dir is not None:
        root_path = Path(workspace_dir)
        root_path.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Creating FilesystemBackend(root_dir=%s, virtual_mode=True) "
            "bound to workspace",
            root_path,
        )
        return FilesystemBackend(root_dir=root_path, virtual_mode=True)

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


#: SDK decision types accepted by ``HumanInTheLoopMiddleware``.
_ALLOWED_DECISION_TYPES = ("approve", "edit", "reject", "respond")


def _map_interrupt_on(rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Convert interrupt rule dicts to an ``interrupt_on`` mapping.

    Each dict follows the (removed) ``InterruptRule`` shape:

    - ``tool_name``: name of the tool
    - ``require_approval``: whether to interrupt
    - ``allowed_decisions`` (optional): subset of
      ``("approve", "edit", "reject", "respond")`` restricting which
      decisions an operator may submit; defaults to all four.
    - ``description`` (optional): custom approval-request description.
    - ``args_schema`` (optional): JSON schema applied when ``edit`` is
      allowed.

    Plain rules map to ``True`` (all decisions open — legacy behaviour).
    Rules with ``allowed_decisions`` map to the SDK's ``InterruptOnConfig``
    TypedDict so the approval policy survives the config → SDK translation
    and is enforced by the middleware.  Invalid decision lists raise
    ``ValueError`` instead of being silently dropped.
    """
    interrupt_on: dict[str, Any] = {}
    for rule in rules:
        tool_name = rule.get("tool_name", "")
        if not rule.get("require_approval", False) or not tool_name:
            continue
        allowed = rule.get("allowed_decisions")
        if allowed is None:
            interrupt_on[tool_name] = True
            continue
        if isinstance(allowed, str):
            allowed = [allowed]
        allowed = list(allowed)
        invalid = [d for d in allowed if d not in _ALLOWED_DECISION_TYPES]
        if not allowed or invalid:
            msg = (
                f"Invalid `allowed_decisions` for tool {tool_name!r}: "
                f"{allowed!r}. Must be a non-empty subset of "
                f"{list(_ALLOWED_DECISION_TYPES)}."
            )
            raise ValueError(msg)
        config: dict[str, Any] = {"allowed_decisions": allowed}
        if rule.get("description"):
            config["description"] = rule["description"]
        if rule.get("args_schema"):
            config["args_schema"] = rule["args_schema"]
        interrupt_on[tool_name] = config
    return interrupt_on if interrupt_on else None


def _compute_excluded_tools(
    tools_cfg: dict[str, Any],
    backend_type: BackendType,
    *,
    enable_subagents: bool = False,
) -> frozenset[str]:
    """Determine which built-in tools must be excluded.

    ``tools_cfg`` is the ``agent.json`` ``tools`` section:

    - ``disabled``: explicit deny-list — always excluded.
    - ``enabled``: when non-empty, acts as a whitelist — every built-in
      tool not listed is excluded.
    - ``execute`` is always excluded on non-sandbox backends regardless
      of configuration (sandbox hardening).
    - ``task`` is excluded when ``enable_subagents`` is false so the
      agent cannot delegate to subagents that were never injected.

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
    # Subagent gating: when subagents are not enabled, the ``task``
    # tool must be excluded so the LLM cannot attempt delegation.
    if not enable_subagents:
        excluded = excluded | frozenset({"task"})
    return excluded


def _resolve_backend_type(settings: dict[str, Any]) -> BackendType:
    """Parse ``settings["backend"]["type"]`` defensively."""
    backend_cfg = settings.get("backend", {})
    backend_type_str = backend_cfg.get("type", BackendType.LOCAL)
    try:
        return BackendType(backend_type_str)
    except ValueError:
        return BackendType.LOCAL


# ---------------------------------------------------------------------------
# Planning / TodoList tool (Task 3.4)
# ---------------------------------------------------------------------------


class _TodoItem(BaseModel):
    """Single todo item (module-level so Pydantic v2 can resolve it)."""
    id: str = ""
    content: str = ""
    status: str = "pending"


class _WriteTodosInput(BaseModel):
    """Schema for the write_todos tool (module-level for Pydantic v2)."""
    todos: list[_TodoItem] = Field(description="List of todo items")


def _create_write_todos_tool() -> Any:
    """Create a ``write_todos`` tool for structured task planning.

    The SDK does not ship a built-in TodoListMiddleware, so we provide
    a minimal LangChain tool that the agent can use to maintain a
    structured todo list.  The todos are stored in-memory (per-agent
    instance) and surfaced in the conversation context.
    """
    from langchain_core.tools import StructuredTool

    # Per-agent todo store — shared across invocations within the same
    # agent graph instance.
    _todos: list[dict[str, Any]] = []

    def _write_todos(todos: list[_TodoItem]) -> str:
        """Replace the current todo list with the provided items."""
        _todos.clear()
        for item in todos:
            _todos.append({
                "id": item.id,
                "content": item.content,
                "status": item.status,
            })
        return f"Updated {len(_todos)} todo(s)"

    return StructuredTool.from_function(
        func=_write_todos,
        name="write_todos",
        description="Write or update a structured todo list for task planning.",
        args_schema=_WriteTodosInput,
    )


# ---------------------------------------------------------------------------
# A2UI (Agent-to-User Interface) tool
# ---------------------------------------------------------------------------


class _SendA2uiInput(BaseModel):
    """Schema for the send_a2ui tool (module-level for Pydantic v2)."""

    surface_title: str = Field(description="Short title describing the purpose of this UI")
    components: list[dict[str, Any]] = Field(
        description=(
            "Flat A2UI v0.9 component list (JSON objects); exactly one "
            "component must have id 'root'"
        )
    )
    data: dict[str, Any] | None = Field(
        default=None,
        description="Initial data model; properties may bind via {'path': '/key'}",
    )


def _create_send_a2ui_tool() -> Any:
    """Create a ``send_a2ui`` tool for rendering interactive UI cards.

    Control-plane only: the tool body never touches the backend or
    filesystem.  The actual A2UI envelopes are assembled and projected
    by the SSE streaming layer (see ``chat_router``) when the tool call
    is detected — the same pattern used for ``write_todos``.
    """
    from langchain_core.tools import StructuredTool

    def _send_a2ui(
        surface_title: str,
        components: list[dict[str, Any]],
        data: dict[str, Any] | None = None,
    ) -> str:
        """Render an interactive UI card (A2UI) to the user.

        Use it to ask the user a question, collect form input or request
        confirmation.  The user's interaction result will arrive as a
        follow-up message.
        """
        return (
            f"UI card '{surface_title}' rendered to the user. Wait for the "
            "user's interaction; the result will arrive as a follow-up message."
        )

    return StructuredTool.from_function(
        func=_send_a2ui,
        name="send_a2ui",
        description=(
            "Render an interactive UI card (A2UI) to the user for questions, "
            "confirmations or form collection. The user's interaction result "
            "arrives as a follow-up message."
        ),
        args_schema=_SendA2uiInput,
    )


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
        sandbox_backend: Any = None,
        **kwargs: Any,
    ) -> CompiledStateGraph:
        """Create a deepagents agent from an ``agent.json`` configuration.

        Mapping summary
        ---------------
        | Config field                    | ``create_deep_agent`` param    |
        |---------------------------------|--------------------------------|
        | ``agent_config["model"]``       | ``model``                      |
        | ``agent_config["tools"]``       | ``_ToolExclusionMiddleware``    |
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
            kernel files (``bootstrap.md`` / ``agent.md``, plus legacy
            ``profile.md`` / ``soul.md`` when present)
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
        # the Skills Middleware.  Paths are relative to the backend root
        # (the workspace directory).
        if workspace is not None or workspace_dir is not None:
            effective_ws_dir = (
                workspace.workspace_dir if workspace is not None
                else workspace_dir
            )
            # When the whole-workspace sandbox mirror is active, files live
            # under the container root (e.g. /home/gem) instead of the
            # backend root — prefix the SDK memory/skills paths so the
            # middleware reads them where the sync engine pushes them.
            backend_cfg = settings.get("backend", {}) or {}
            path_prefix = ""
            if backend_cfg.get("type") == "sandbox":
                file_sync = backend_cfg.get("file_sync", {}) or {}
                if file_sync.get("enabled", True) is not False:
                    path_prefix = str(
                        file_sync.get("container_root", "/home/gem")
                    ).rstrip("/")
            if kwargs.get("memory") is None:
                kernel_paths = [
                    f"{path_prefix}/{name}"
                    for name in ALL_KERNEL_FILE_NAMES
                    if (effective_ws_dir / name).exists()
                ]
                # Also load memory files via SDK's MemoryMiddleware.
                # The agent can update these files using standard
                # edit_file / write_file tools through the backend.
                memory_paths = []
                memory_dir = effective_ws_dir / MEMORY_DIR_NAME
                if memory_dir.exists():
                    for mem_file in ["MEMORY.md", "USER.md"]:
                        if (memory_dir / mem_file).exists():
                            memory_paths.append(
                                f"{path_prefix}/{MEMORY_DIR_NAME}/{mem_file}"
                            )
                all_memory_paths = kernel_paths + memory_paths
                if all_memory_paths:
                    kwargs["memory"] = all_memory_paths
            if kwargs.get("skills") is None:
                skills_dir = effective_ws_dir / SKILLS_DIR_NAME
                if skills_dir.exists() and skills_dir.is_dir():
                    kwargs["skills"] = [f"{path_prefix}/{SKILLS_DIR_NAME}/"]

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
        # If a pre-created sandbox backend is provided (from session manager),
        # use it directly instead of creating a new one via _create_backend.
        if sandbox_backend is not None:
            backend = sandbox_backend
            logger.info("Using pre-created sandbox backend from session manager")
        else:
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
        # When a sandbox backend is provided, force SANDBOX backend type
        # so the execute tool is not excluded.
        if sandbox_backend is not None:
            backend_type = BackendType.SANDBOX
        else:
            backend_type = _resolve_backend_type(settings)
        excluded = _compute_excluded_tools(
            agent_config.get("tools", {}) or {},
            backend_type,
            enable_subagents=bool(settings.get("enable_subagents")),
        )
        extra_middleware: list[AgentMiddleware[Any, Any, Any]] = list(
            kwargs.pop("middleware", []) or []
        )
        if excluded:
            logger.info(
                "Excluding disabled built-in tools via "
                "tool-exclusion middleware: %s",
                sorted(excluded),
            )
            extra_middleware.append(
                _ToolExclusionMiddleware(excluded=excluded)
            )

        # --- Skill exclusion (per-agent skill toggling) ----------------
        # Installed workspace skills can be disabled without uninstalling
        # them (``agent.json`` ``skills.disabled``).  The SDK loads every
        # skill under ``skills/``; this middleware drops the disabled
        # ones from the loaded index so they never reach the prompt.
        skills_cfg = agent_config.get("skills", {}) or {}
        disabled_skills = frozenset(skills_cfg.get("disabled", []) or [])
        if disabled_skills:
            logger.info(
                "Excluding disabled workspace skills via "
                "skill-exclusion middleware: %s",
                sorted(disabled_skills),
            )
            extra_middleware.append(
                _SkillExclusionMiddleware(disabled=disabled_skills)
            )

        # --- Planning / TodoList (Task 3.4) --------------------------
        # Opt-in via ``settings.enable_planning: true``.  Adds a
        # ``write_todos`` tool so the agent can maintain a structured
        # todo list across turns.  The SDK does not ship a built-in
        # TodoListMiddleware, so we provide a minimal implementation as
        # a custom tool.
        custom_tools: list[Any] | None = settings.get("custom_tools") or kwargs.pop("tools", None)
        if settings.get("enable_planning"):
            custom_tools = list(custom_tools or [])
            custom_tools.append(_create_write_todos_tool())
            logger.info("Planning enabled: write_todos tool added")

        # --- A2UI interactive surfaces -------------------------------
        # Enabled by default; opt out via ``settings.enable_a2ui: false``.
        # Adds the ``send_a2ui`` control-plane tool so the agent can render
        # interactive cards (questions / forms / confirmations) in the
        # chat stream.  Subagent graphs are built independently by the
        # registry and never inherit this tool.
        if settings.get("enable_a2ui", True):
            custom_tools = list(custom_tools or [])
            custom_tools.append(_create_send_a2ui_tool())
            logger.info("A2UI enabled: send_a2ui tool added")

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
        system_prompt = settings.get("system_prompt") or None
        if settings.get("enable_planning"):
            # Guide the model to actually use write_todos for multi-step
            # tasks so the todo breakdown is visible in the chat stream.
            _plan_hint = (
                "\n\n## 任务拆解要求\n"
                "对于多步骤任务，先使用 write_todos 工具创建结构化任务计划，"
                "拆解执行步骤并标记状态，再逐步执行。"
            )
            system_prompt = (
                f"{system_prompt}{_plan_hint}" if system_prompt else _plan_hint
            )
        if settings.get("enable_a2ui", True):
            # Teach the model the A2UI component conventions so the
            # generated payloads validate against the basic catalog.
            _a2ui_hint = (
                "\n\n## 交互式 UI（A2UI）要求\n"
                "当你需要向用户提问、请求确认或收集信息（表单）时，必须优先调用 send_a2ui 工具\n"
                "生成交互卡片，而不是用纯文本提问。components 是扁平的 A2UI v0.9 组件列表，\n"
                "必须恰好有一个组件 id 为 \"root\"；组件间用 id 互相引用：Row/Column/List 容器只能用 children（id 数组，如 [\"a\",\"b\"]），绝不能用 child；\n"
                "Button/Card 等单子插槽只能用 child（单个 id），绝不能用 children，混用会导致校验失败。\n"
                "可用组件：Text(text, variant: h1|h2|h3|body|caption)、Button(child, variant: primary|default|borderless, action)、"
                "TextField(label, value, variant: shortText|longText|obscured)、CheckBox(label, value)、"
                "ChoicePicker(label, options:[{label,value}], value(已选值数组，可绑定 data 中的数组), variant: mutuallyExclusive|multipleSelection)、Slider、"
                "DateTimeInput、Card(child)、Row/Column(children, justify: start|center|end|spaceBetween|spaceAround|spaceEvenly|stretch, align: start|center|end|stretch，"
                "禁用 flex-end 等 CSS 值)、List、Tabs、Divider、Image。\n"
                "动态值：属性可写字面量，也可用 {\"path\": \"/键名\"} 绑定 data 参数里的数据模型。\n"
                "按钮回传格式：action={\"event\": {\"name\": \"<事件名>\", \"context\": {\"字段\": {\"path\": \"/键名\"}}}}，\n"
                "用户点击后其填写的数据会作为新消息回传给你，届时继续处理。\n"
                "最小示例：components=[{\"id\":\"root\",\"component\":\"Column\",\"children\":[\"t\",\"b\"]},"
                "{\"id\":\"t\",\"component\":\"Text\",\"text\":\"是否继续执行？\"},"
                "{\"id\":\"b\",\"component\":\"Button\",\"child\":\"bl\",\"variant\":\"primary\","
                "\"action\":{\"event\":{\"name\":\"confirm\",\"context\":{}}}},"
                "{\"id\":\"bl\",\"component\":\"Text\",\"text\":\"确认\"}]"
            )
            system_prompt = (
                f"{system_prompt}{_a2ui_hint}" if system_prompt else _a2ui_hint
            )
        create_kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
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
