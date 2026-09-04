"""Workspace runtime: independent working space for each Agent.

Each :class:`Workspace` encapsulates a complete, isolated runtime environment
for a single agent, including conversation management (:class:`ChatManager`),
external capability drivers (:class:`DriverManager`), and pluggable extensions
(:class:`PluginRegistry`).

The workspace owns its own directory under
``.agentcore/workspace/agent/{id}/`` (kernel files, skills and agent.json —
visible to the agent); session data is persisted separately under
``.agentcore/data/agents/{id}/sessions/`` (hidden from the agent).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentcore.runtime import paths

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON persistence helpers (mirrors repository.py pattern)
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, data: object) -> None:
    """Write *data* as JSON to *path* atomically (temp-file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp", dir=str(path.parent), prefix=f".{path.stem}_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if path.exists():
            path.unlink()
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Any:
    """Read and parse a JSON file, returning ``None`` if missing."""
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Kernel files (bootstrap.md / agent.md)
#
# Each workspace directory holds up to two "kernel" markdown files whose
# contents are dynamically injected as the agent's system_prompt whenever
# the agent graph is (re)created.
#
# ``bootstrap.md`` is special: it is seeded exactly once on first workspace
# creation to guide the user through initial setup.  Once the agent (or the
# user) removes it, it is never re-created — the ``.bootstrap_seeded``
# marker file records that seeding already happened.
#
# ``agent.md`` is the single identity/persona file (name, role, behaviour,
# tone).  It is injected at the *reference* level via the SDK's ``memory=[]``
# mechanism, so strong "instructions" (e.g. must-always rules) belong in
# ``settings.system_prompt`` instead.
#
# Legacy files (``profile.md`` / ``soul.md``) from workspaces created before
# the 2026-08 kernel simplification are still read and editable for
# backwards compatibility, but no new workspace seeds them.
# ---------------------------------------------------------------------------

KERNEL_FILE_NAMES: tuple[str, ...] = (
    "bootstrap.md",
    "agent.md",
)

# Files seeded by older versions; kept readable/editable when present so
# existing workspaces keep working unchanged.
LEGACY_KERNEL_FILE_NAMES: tuple[str, ...] = (
    "profile.md",
    "soul.md",
)

ALL_KERNEL_FILE_NAMES: tuple[str, ...] = KERNEL_FILE_NAMES + LEGACY_KERNEL_FILE_NAMES

KERNEL_FILE_HEADINGS: dict[str, str] = {
    "bootstrap.md": "# 欢迎",
    "agent.md": "# Agent 身份",
    "profile.md": "# Profile 配置",
    "soul.md": "# 核心人设",
}

DEFAULT_KERNEL_FILES: dict[str, str] = {
    "agent.md": (
        "# Agent 身份\n\n"
        "你是一个乐于助人的 AI 助手，名叫 AgentCore。\n\n"
        "## 行为准则\n"
        "- 保持友好和专业\n"
        "- 回答简洁明了\n\n"
        "## 性格特点\n"
        "- 友善、耐心\n"
        "- 善于倾听和理解\n"
    ),
}

# ---------------------------------------------------------------------------
# Bootstrap guidance (bootstrap.md)
#
# Seeded only on first workspace creation; the agent may delete it once
# the user finishes initial setup (its absence is then permanent).
# ---------------------------------------------------------------------------

BOOTSTRAP_MD_NAME = "bootstrap.md"
BOOTSTRAP_SEEDED_FLAG_NAME = ".bootstrap_seeded"

DEFAULT_BOOTSTRAP_MD = """# 欢迎使用 AgentCore！

这是我们的第一次对话。请友好地与用户打招呼，并按下面四个步骤引导用户完成初始设置。

## 第一步：确定我的名字与性格

询问用户想给我起的名字和性格偏好，然后使用 write_file 工具更新：

- `/agent.md` — 我的身份与人设（名字、角色、行为准则、性格）

## 第二步：了解用户的偏好

询问用户的 profile 偏好（语言风格、回答习惯等），然后使用 write_file 工具更新：

- `/memory/USER.md` — 用户的偏好配置

> 提示：`/memory/MEMORY.md` 与 `/memory/sessions/` 目录存放长期记忆与历史会话归档。缺少上下文时，可先用 grep 或 read_file 在其中检索历史信息。

## 第三步：告知模型配置方式

告诉用户：可以在「模型」页面或 Chat 页的 Agent 选择器中，为我配置或切换 AI 模型。

## 第四步：删除本文件，标志初始化完成

以上步骤全部完成后，**必须调用 delete 工具删除 `/bootstrap.md`**。删除后即代表初始化结束，之后不要再提及引导流程，直接开始正常工作。

> 如果用户希望跳过某一步，尊重用户的选择并继续下一步；即使用户全部跳过，最后也要删除本文件。
"""


# ---------------------------------------------------------------------------
# Heartbeat checklist (HEARTBEAT.md)
#
# Seeded once on first workspace creation.  The scheduled heartbeat
# (runtime/heartbeat.py) feeds this file's content to the agent as a
# periodic query.  A missing or empty file is the natural safety valve:
# the heartbeat run is skipped silently.  Once the user (or agent)
# deletes it, the ``.heartbeat_seeded`` marker ensures it is never
# re-created — same pattern as bootstrap.md.
# ---------------------------------------------------------------------------

HEARTBEAT_MD_NAME = "HEARTBEAT.md"
HEARTBEAT_SEEDED_FLAG_NAME = ".heartbeat_seeded"

DEFAULT_HEARTBEAT_MD = """# Heartbeat checklist

这是一次定时心跳巡检。请按以下清单快速检查，若一切正常只需简短汇报：

- 检查 memory/ 目录中是否有需要跟进的事项
- 检查待办事项是否卡住
- 若距上次交互已超过 8 小时，可以做一次轻量 check-in
- 没有需要汇报的内容时，直接回复 HEARTBEAT_OK 即可
"""


# ---------------------------------------------------------------------------
# Agent configuration file (agent.json)
#
# Each workspace directory holds an ``agent.json`` file describing the
# agent's runtime configuration: the model to use, which built-in tools
# are enabled/disabled, and free-form settings.  The file is created with
# sensible defaults on first workspace creation and is never overwritten
# afterwards — user edits survive restarts.
# ---------------------------------------------------------------------------

AGENT_JSON_NAME = "agent.json"

# ---------------------------------------------------------------------------
# MCP configuration file (mcp.json)
#
# Per-workspace MCP server registry.  Shape::
#
#     {
#         "<server_id>": {
#             "server_id": "...",
#             "name": "...",
#             "transport": "stdio" | "sse" | "streamable_http",
#             "command": "npx -y ...",       # stdio transport
#             "url": "http://...",           # sse / streamable_http
#             "env": {...},                  # optional (stdio)
#             "headers": {...},              # optional (http transports)
#             "enabled": true
#         }
#     }
# ---------------------------------------------------------------------------

MCP_JSON_NAME = "mcp.json"

DEFAULT_AGENT_JSON: dict[str, Any] = {
    "model": {
        "provider": "openai",
        "name": "qwen3.6-plus",
        "base_url": "https://coding.dashscope.aliyuncs.com/v1",
        "api_key_env": "AGENTCORE_LLM_API_KEY",
    },
    "tools": {
        "enabled": [],
        "disabled": [],
    },
    "skills": {
        "disabled": [],
    },
    "settings": {},
}


# ---------------------------------------------------------------------------
# Skills directory
#
# Each workspace holds a ``skills/`` directory whose sub-directories contain
# ``SKILL.md`` files (Agent Skills specification).  The deepagents SDK's
# ``SkillsMiddleware`` scans this directory (via the backend, which is
# rooted at the workspace) and lists every skill in the system prompt.
# ---------------------------------------------------------------------------

SKILLS_DIR_NAME = "skills"

# ---------------------------------------------------------------------------
# Memory directory
#
# Each workspace holds a ``memory/`` directory for persistent agent memory.
# Inspired by QwenPaw/ReMe's file-based memory approach:
#
#   memory/
#   ├── MEMORY.md          ← long-term facts, preferences, knowledge
#   ├── USER.md            ← user profile (habits, relationships, style)
#   └── sessions/          ← daily session archives
#       └── YYYY-MM-DD.md
#
# The agent can read/write these files via the built-in file tools
# (edit_file / write_file).  Files are loaded into the system prompt by
# the SDK's MemoryMiddleware at agent startup and on each new conversation
# turn.  Files are plain Markdown so users can browse and edit them in the
# Files page.
# ---------------------------------------------------------------------------

MEMORY_DIR_NAME = "memory"
MEMORY_FILE_NAME = "MEMORY.md"
USER_FILE_NAME = "USER.md"
SESSIONS_ARCHIVE_DIR_NAME = "sessions"

DEFAULT_MEMORY_MD = "# Long-term Memory\n\nThis file stores long-term facts, preferences, and knowledge accumulated through interactions.\n\n"
DEFAULT_USER_MD = "# User Profile\n\nThis file stores the user's habits, preferences, communication style, and relationship context.\n\n"

DEFAULT_EXAMPLE_SKILL = """---
name: example
description: 一个示例技能，演示 skills 目录的结构与用法
---

# 示例技能

这是一个示例技能文件。

## 何时使用

- 用户询问如何创建或使用技能时，可以参考本文件的结构。

## 技能结构

每个技能是一个独立目录，包含一个 ``SKILL.md`` 文件：

```
skills/
└── example/
    └── SKILL.md
```

``SKILL.md`` 必须以 YAML frontmatter 开头，包含 ``name``（与目录同名，
小写字母、数字和连字符）与 ``description``（简述技能的用途与触发场景）。
"""



# ---------------------------------------------------------------------------
# ChatManager
#
# Persistence layout (per-agent ``sessions/`` directory):
#
# * ``index.json``            — lightweight metadata index
#                               (session_id → agent_id / timestamps /
#                               message_count)
# * ``{session_id}.json``     — one file per session (meta + messages)
#
# Appending a message only rewrites the affected session file plus the
# small index — never the whole conversation set (the legacy single-file
# ``sessions.json`` was O(total size) per message).  Messages are
# buffered in memory and flushed to disk on write (no debounce).
# The legacy ``sessions.json`` is migrated transparently on load.
# ---------------------------------------------------------------------------

SESSION_INDEX_NAME = "index.json"
LEGACY_SESSIONS_FILE_NAME = "sessions.json"


def _session_file_name(session_id: str) -> str:
    """Map *session_id* to a safe per-session file name.

    Session ids normally come from ``uuid4().hex``, but user-supplied ids
    are possible via the chat API — anything outside ``[A-Za-z0-9_-]`` is
    replaced so the id can never escape the sessions directory.
    """
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in session_id)


@dataclass
class ChatManager:
    """Manage all conversations within a single Workspace.

    Messages follow the same shape as :mod:`agentcore.runtime.chat_router`:

    .. code-block:: python

        {
            "role": "user" | "assistant" | "system",
            "content": "...",
            "timestamp": "<ISO-8601>",
            "tool_calls": [{"name": ..., "args": ..., "id": ...}],  # optional
        }
    """

    workspace_id: str
    sessions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _session_meta: dict[str, dict[str, str]] = field(default_factory=dict)
    _persist_dir: Path | None = field(default=None, repr=False)

    # -- persistence --------------------------------------------------------

    def _index_path(self) -> Path | None:
        if self._persist_dir is None:
            return None
        return self._persist_dir / SESSION_INDEX_NAME

    def _session_path(self, session_id: str) -> Path | None:
        if self._persist_dir is None:
            return None
        return self._persist_dir / f"{_session_file_name(session_id)}.json"

    def _write_index(self) -> None:
        """Rewrite the metadata index from in-memory state."""
        path = self._index_path()
        if path is None:
            return
        index: dict[str, Any] = {}
        for sid, meta in self._session_meta.items():
            index[sid] = {
                "session_id": meta.get("session_id", sid),
                "agent_id": meta.get("agent_id", ""),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
                "message_count": len(self.sessions.get(sid, [])),
            }
        _atomic_write_json(path, index)

    def _flush_session(self, session_id: str) -> None:
        """Persist one session file and refresh the index."""
        path = self._session_path(session_id)
        if path is None:
            return
        meta = self._session_meta.get(session_id, {})
        _atomic_write_json(
            path,
            {
                "session_id": meta.get("session_id", session_id),
                "agent_id": meta.get("agent_id", ""),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
                "messages": self.sessions.get(session_id, []),
            },
        )
        self._write_index()

    def _ingest_session(self, sid: str, data: dict[str, Any]) -> None:
        """Load one session dict into memory (shared by all load paths)."""
        self.sessions[sid] = list(data.get("messages", []) or [])
        self._session_meta[sid] = {
            "session_id": data.get("session_id", sid),
            "agent_id": data.get("agent_id", ""),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
        }

    def load(self) -> None:
        """Load sessions from the workspace directory.

        Reads the per-session files (via ``index.json`` when present,
        otherwise by scanning).  A legacy single-file ``sessions.json``
        is migrated in place: its sessions are imported, re-persisted in
        the new per-file layout, and the legacy file is removed.
        """
        if self._persist_dir is None:
            return

        # 1) Per-session files (new layout).
        index = _read_json(self._index_path())
        if isinstance(index, dict) and index:
            candidates = [
                self._persist_dir / f"{_session_file_name(sid)}.json"
                for sid in index
            ]
        else:
            candidates = [
                p
                for p in self._persist_dir.glob("*.json")
                if p.name != SESSION_INDEX_NAME
            ]
        for path in candidates:
            data = _read_json(path)
            if not isinstance(data, dict):
                continue
            sid = data.get("session_id") or path.stem
            self._ingest_session(sid, data)

        # 2) Legacy single-file layout — migrate once.
        legacy_path = self._persist_dir / LEGACY_SESSIONS_FILE_NAME
        if legacy_path.exists():
            raw = _read_json(legacy_path)
            legacy_sessions = (
                raw.get("sessions", {}) if isinstance(raw, dict) else {}
            )
            migrated = 0
            for sid, data in legacy_sessions.items():
                if sid in self.sessions or not isinstance(data, dict):
                    continue
                self._ingest_session(sid, data)
                self._flush_session(sid)
                migrated += 1
            try:
                legacy_path.unlink()
                logger.info(
                    "ChatManager[%s]: migrated legacy sessions.json "
                    "(%d session(s)) to per-file layout",
                    self.workspace_id,
                    migrated,
                )
            except OSError:
                logger.warning(
                    "ChatManager[%s]: could not remove legacy sessions.json",
                    self.workspace_id,
                )

        if self.sessions:
            logger.info(
                "ChatManager[%s]: loaded %d session(s)",
                self.workspace_id,
                len(self.sessions),
            )

    def save(self) -> None:
        """Persist all sessions (per-file layout) to disk."""
        if self._persist_dir is None:
            return
        for sid in self.sessions:
            path = self._session_path(sid)
            if path is None:
                continue
            meta = self._session_meta.get(sid, {})
            _atomic_write_json(
                path,
                {
                    "session_id": meta.get("session_id", sid),
                    "agent_id": meta.get("agent_id", ""),
                    "created_at": meta.get("created_at", ""),
                    "updated_at": meta.get("updated_at", ""),
                    "messages": self.sessions[sid],
                },
            )
        self._write_index()

    # -- public API ---------------------------------------------------------

    def create_session(
        self,
        session_id: str,
        *,
        agent_id: str = "",
    ) -> None:
        """Create a new empty session.

        If *session_id* already exists this is a no-op.
        """
        if session_id in self.sessions:
            return
        now = _now_iso()
        self.sessions[session_id] = []
        self._session_meta[session_id] = {
            "session_id": session_id,
            "agent_id": agent_id,
            "created_at": now,
            "updated_at": now,
        }
        self._flush_session(session_id)
        logger.debug("ChatManager[%s]: session %s created", self.workspace_id, session_id)

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        **metadata: Any,
    ) -> dict[str, Any]:
        """Append a message to *session_id* and persist.

        Parameters
        ----------
        session_id:
            Target session.  Must exist (call :meth:`create_session` first).
        role:
            Message role — ``"user"``, ``"assistant"``, or ``"system"``.
        content:
            Text content of the message.
        **metadata:
            Extra fields merged into the message dict (e.g. ``tool_calls``).

        Returns
        -------
        dict
            The message dict that was appended.

        Raises
        ------
        KeyError
            If *session_id* does not exist.
        """
        if session_id not in self.sessions:
            raise KeyError(f"Session {session_id!r} does not exist")

        msg: dict[str, Any] = {
            "role": role,
            "content": content,
            "timestamp": _now_iso(),
        }
        # Merge optional extras (tool_calls, etc.)
        for key, value in metadata.items():
            if value is not None:
                msg[key] = value

        self.sessions[session_id].append(msg)

        # Update session timestamp.
        meta = self._session_meta.setdefault(session_id, {})
        meta["updated_at"] = msg["timestamp"]

        # Flush only the affected session file (+ index) — never the
        # whole conversation set.
        self._flush_session(session_id)
        return msg

    def get_history(
        self,
        session_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the most recent *limit* messages from *session_id*.

        Raises
        ------
        KeyError
            If *session_id* does not exist.
        """
        if session_id not in self.sessions:
            raise KeyError(f"Session {session_id!r} does not exist")
        messages = self.sessions[session_id]
        return messages[-limit:] if len(messages) > limit else list(messages)

    def list_sessions(self) -> list[str]:
        """Return all session IDs managed by this workspace."""
        return list(self.sessions.keys())

    def get_session_info(self, session_id: str) -> dict[str, Any] | None:
        """Return metadata for a single session, or ``None``."""
        meta = self._session_meta.get(session_id)
        if meta is None:
            return None
        return {
            **meta,
            "message_count": len(self.sessions.get(session_id, [])),
        }

    def delete_session(self, session_id: str) -> None:
        """Delete a session and its messages.

        Silently ignores unknown session IDs.
        """
        self.sessions.pop(session_id, None)
        self._session_meta.pop(session_id, None)
        path = self._session_path(session_id)
        if path is not None and path.exists():
            try:
                path.unlink()
            except OSError:
                logger.warning(
                    "ChatManager[%s]: failed to remove session file %s",
                    self.workspace_id,
                    path,
                )
        if self._persist_dir is not None:
            self._write_index()
        logger.debug(
            "ChatManager[%s]: session %s deleted", self.workspace_id, session_id
        )


# ---------------------------------------------------------------------------
# DriverManager (placeholder for MCP / ACP integration)
# ---------------------------------------------------------------------------


@dataclass
class DriverManager:
    """Manage external-capability drivers (MCP, ACP, …).

    This is a **placeholder** implementation — concrete driver lifecycle
    logic will be added in a later phase.  The interface is stable so that
    other modules can already depend on it.
    """

    workspace_id: str
    drivers: dict[str, Any] = field(default_factory=dict)

    def register_driver(self, name: str, driver: Any) -> None:
        """Register a driver under *name*."""
        if name in self.drivers:
            logger.warning(
                "DriverManager[%s]: driver %r already registered — overwriting",
                self.workspace_id,
                name,
            )
        self.drivers[name] = driver
        logger.info("DriverManager[%s]: registered driver %r", self.workspace_id, name)

    def get_driver(self, name: str) -> Any | None:
        """Return the driver registered under *name*, or ``None``."""
        return self.drivers.get(name)

    def list_drivers(self) -> list[str]:
        """Return all registered driver names."""
        return list(self.drivers.keys())

    def unregister_driver(self, name: str) -> None:
        """Remove a driver.  Silently ignores unknown names."""
        self.drivers.pop(name, None)


# ---------------------------------------------------------------------------
# PluginRegistry (placeholder)
# ---------------------------------------------------------------------------


@dataclass
class PluginRegistry:
    """Manage per-workspace plugins.

    This is a **placeholder** — the concrete plugin discovery and lifecycle
    will be implemented in a later phase.
    """

    workspace_id: str
    plugins: dict[str, Any] = field(default_factory=dict)

    def register(self, name: str, plugin: Any) -> None:
        """Register a plugin under *name*."""
        if name in self.plugins:
            logger.warning(
                "PluginRegistry[%s]: plugin %r already registered — overwriting",
                self.workspace_id,
                name,
            )
        self.plugins[name] = plugin
        logger.info("PluginRegistry[%s]: registered plugin %r", self.workspace_id, name)

    def get(self, name: str) -> Any | None:
        """Return the plugin registered under *name*, or ``None``."""
        return self.plugins.get(name)

    def list_plugins(self) -> list[str]:
        """Return all registered plugin names."""
        return list(self.plugins.keys())

    def unregister(self, name: str) -> None:
        """Remove a plugin.  Silently ignores unknown names."""
        self.plugins.pop(name, None)


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class Workspace:
    """Independent working space for a single Agent.

    Each Agent gets its own :class:`Workspace` with isolated conversation
    storage, driver registry, and plugin registry.  The workspace owns a
    directory on disk (``.agentcore/workspace/agent/{workspace_id}/``)
    holding agent-visible files (kernel files, ``skills/``,
    ``agent.json``); session data is persisted outside the workspace at
    ``.agentcore/data/agents/{workspace_id}/sessions/`` so the agent's
    built-in file tools can never see it.

    Lifecycle
    ---------
    1. Construct the workspace (``__init__``).
    2. Call :meth:`initialize` to load persisted data from disk.
    3. Use the workspace (chat, drivers, plugins).
    4. Call :meth:`shutdown` to flush data and release resources.
    5. Optionally call :meth:`reload` for hot-reload without full restart.
    """

    def __init__(
        self,
        workspace_id: str,
        agent_id: str,
        workspace_dir: Path | None = None,
        repository: Any = None,
        chat_manager: ChatManager | None = None,
        chat_router: Any = None,
        mcp_manager: Any = None,
        driver_manager: DriverManager | None = None,
        plugin_registry: PluginRegistry | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.agent_id = agent_id
        self.workspace_dir = workspace_dir or paths.get_agent_workspace_dir(
            workspace_id
        )
        # Sessions live outside the agent-visible workspace.
        self.sessions_dir = paths.get_agent_sessions_dir(agent_id)
        self.repository = repository
        self.chat_router = chat_router
        self.mcp_manager = mcp_manager

        # Sub-services (accept injected instances or create defaults).
        self.chat_manager = chat_manager or ChatManager(
            workspace_id=workspace_id,
            _persist_dir=self.sessions_dir,
        )
        self.driver_manager = driver_manager or DriverManager(
            workspace_id=workspace_id,
        )
        self.plugin_registry = plugin_registry or PluginRegistry(
            workspace_id=workspace_id,
        )

        # Ensure the workspace and sessions directories exist.
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        # Kernel files — seed defaults on first creation, then load them
        # into memory so they can be injected via the SDK's memory=[]
        # mechanism.  bootstrap.md is seeded at most once per workspace.
        self._ensure_default_kernel_files()
        self._ensure_bootstrap_file()
        self._ensure_heartbeat_file()
        self._kernel_files: dict[str, str] = self._load_kernel_files()

        # Agent configuration (agent.json) — seed the default config on
        # first creation; existing files are never touched.
        self._ensure_agent_json()

        # Skills — seed the ``skills/`` directory (with an example skill
        # on first creation) so the SDK's skills=[] mechanism can pick
        # skills up from the workspace.
        self._ensure_default_skills_dir()

        # Memory — seed the ``memory/`` directory with default MEMORY.md
        # and USER.md files for persistent agent memory (Phase 1-3).
        self._ensure_memory_dir()

        self._initialized = False

        logger.debug(
            "Workspace[%s]: created for agent %s at %s",
            workspace_id,
            agent_id,
            self.workspace_dir,
        )

    # -- lifecycle ----------------------------------------------------------

    async def initialize(self) -> None:
        """Initialise the workspace.

        Loads persisted session data from disk and marks the workspace as
        ready.  Safe to call multiple times (subsequent calls are no-ops).
        """
        if self._initialized:
            logger.debug("Workspace[%s]: already initialized", self.workspace_id)
            return

        logger.info("Workspace[%s]: initializing …", self.workspace_id)

        # Load persisted chat sessions.
        self.chat_manager.load()

        # Future: load drivers, plugins, etc.

        self._initialized = True
        logger.info("Workspace[%s]: initialized", self.workspace_id)

    async def shutdown(self) -> None:
        """Shut down the workspace.

        Flushes all in-memory data to disk and releases resources.
        """
        if not self._initialized:
            return

        logger.info("Workspace[%s]: shutting down …", self.workspace_id)

        # Persist chat sessions.
        self.chat_manager.save()

        # Future: stop drivers, unload plugins, etc.

        self._initialized = False
        logger.info("Workspace[%s]: shut down complete", self.workspace_id)

    async def reload(self) -> None:
        """Hot-reload the workspace.

        Re-reads persisted data from disk without a full restart cycle.
        Drivers and plugins will be re-initialised in future phases.
        """
        logger.info("Workspace[%s]: reloading …", self.workspace_id)

        # Re-read kernel files (bootstrap.md / agent.md, plus legacy
        # profile.md / soul.md when present) so that a subsequently
        # (re)created agent picks up the new system_prompt.
        self._kernel_files = self._load_kernel_files()

        # Re-load chat sessions from disk.
        self.chat_manager.sessions.clear()
        self.chat_manager._session_meta.clear()
        self.chat_manager.load()

        logger.info("Workspace[%s]: reload complete", self.workspace_id)

    # -- kernel files -------------------------------------------------------

    def _ensure_default_kernel_files(self) -> None:
        """Seed missing kernel files with their default content.

        Existing files are never overwritten — user edits survive restarts.
        """
        for name, default_content in DEFAULT_KERNEL_FILES.items():
            path = self.workspace_dir / name
            if path.exists():
                continue
            try:
                path.write_text(default_content, encoding="utf-8")
                logger.info(
                    "Workspace[%s]: created default kernel file %s",
                    self.workspace_id,
                    name,
                )
            except OSError as exc:
                logger.warning(
                    "Workspace[%s]: failed to create default %s: %s",
                    self.workspace_id,
                    name,
                    exc,
                )

    def _ensure_bootstrap_file(self) -> None:
        """Seed ``bootstrap.md`` exactly once per workspace.

        The file is only created on the very first workspace initialisation
        (no ``bootstrap.md`` and no ``.bootstrap_seeded`` marker yet).  If
        the agent or user deletes it afterwards, the marker ensures it is
        never re-created.
        """
        bootstrap_path = self.workspace_dir / BOOTSTRAP_MD_NAME
        flag_path = self.workspace_dir / BOOTSTRAP_SEEDED_FLAG_NAME
        if bootstrap_path.exists() or flag_path.exists():
            return
        try:
            bootstrap_path.write_text(DEFAULT_BOOTSTRAP_MD, encoding="utf-8")
            flag_path.touch()
            logger.info(
                "Workspace[%s]: created bootstrap.md (first-run guidance)",
                self.workspace_id,
            )
        except OSError as exc:
            logger.warning(
                "Workspace[%s]: failed to create %s: %s",
                self.workspace_id,
                BOOTSTRAP_MD_NAME,
                exc,
            )

    def _ensure_heartbeat_file(self) -> None:
        """Seed ``HEARTBEAT.md`` exactly once per workspace.

        Same one-shot semantics as ``bootstrap.md``: the marker file
        records that seeding already happened, so a user-deleted
        checklist stays deleted (and the scheduled heartbeat silently
        skips runs while the file is absent).
        """
        heartbeat_path = self.workspace_dir / HEARTBEAT_MD_NAME
        flag_path = self.workspace_dir / HEARTBEAT_SEEDED_FLAG_NAME
        if heartbeat_path.exists() or flag_path.exists():
            return
        try:
            heartbeat_path.write_text(DEFAULT_HEARTBEAT_MD, encoding="utf-8")
            flag_path.touch()
            logger.info(
                "Workspace[%s]: created HEARTBEAT.md (heartbeat checklist)",
                self.workspace_id,
            )
        except OSError as exc:
            logger.warning(
                "Workspace[%s]: failed to create %s: %s",
                self.workspace_id,
                HEARTBEAT_MD_NAME,
                exc,
            )

    def _load_kernel_files(self) -> dict[str, str]:
        """Read the kernel markdown files from disk.

        Covers the standard files (``bootstrap.md`` / ``agent.md``) plus
        legacy ``profile.md`` / ``soul.md`` when present.  Missing or
        unreadable files are skipped gracefully — the resulting
        system_prompt simply omits them.
        """
        files: dict[str, str] = {}
        for name in ALL_KERNEL_FILE_NAMES:
            path = self.workspace_dir / name
            if not path.exists():
                continue
            try:
                files[name] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning(
                    "Workspace[%s]: failed to read kernel file %s: %s",
                    self.workspace_id,
                    name,
                    exc,
                )
        return files

    def get_system_prompt(self) -> str:
        """Compose the kernel files into a single system prompt.

        Sections appear in ``bootstrap.md`` → ``agent.md`` order, with
        legacy ``profile.md`` / ``soul.md`` appended after them when
        present, separated by ``---`` rules.  Empty or missing files are
        skipped; when nothing is available an empty string is returned.
        """
        parts: list[str] = []
        for name in ALL_KERNEL_FILE_NAMES:
            content = self._kernel_files.get(name)
            if content is None or not content.strip():
                continue
            text = content.strip()
            heading = KERNEL_FILE_HEADINGS[name]
            # Avoid duplicating the section heading when the file content
            # already starts with it (the defaults do).
            if text.startswith(heading):
                parts.append(text)
            else:
                parts.append(f"{heading}\n\n{text}")
        return "\n\n---\n\n".join(parts)

    def read_kernel_file(self, filename: str) -> str | None:
        """Return the on-disk content of a kernel file, or ``None``.

        ``None`` is returned for unknown filenames or missing files.
        """
        if filename not in ALL_KERNEL_FILE_NAMES:
            return None
        path = self.workspace_dir / filename
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def write_kernel_file(self, filename: str, content: str) -> None:
        """Write a kernel file (UTF-8) and refresh the in-memory cache.

        Raises
        ------
        ValueError
            If *filename* is not one of the recognised kernel files.
        """
        if filename not in ALL_KERNEL_FILE_NAMES:
            raise ValueError(
                f"Unknown kernel file {filename!r}; expected one of "
                f"{ALL_KERNEL_FILE_NAMES}"
            )
        path = self.workspace_dir / filename
        path.write_text(content, encoding="utf-8")
        self._kernel_files = self._load_kernel_files()
        logger.info(
            "Workspace[%s]: kernel file %s updated (%d chars)",
            self.workspace_id,
            filename,
            len(content),
        )

    # -- agent.json configuration -------------------------------------------

    def _ensure_agent_json(self) -> None:
        """Create ``agent.json`` with default content when missing.

        When the file exists but its ``model`` section is missing or
        incomplete (e.g. an agent created before model defaults were
        fixed), the missing/empty fields are backfilled from
        :data:`DEFAULT_AGENT_JSON` — user-configured non-empty values are
        never overwritten.

        The seeded ``default`` agent additionally gets
        ``settings.enable_subagents: true`` (opt-in subagent delegation);
        an explicitly set value — whatever it is — is never overwritten.
        """
        path = self.workspace_dir / AGENT_JSON_NAME
        if not path.exists():
            # A console-configured default model (model registry) seeds new
            # agents; otherwise the built-in default model is used.
            from agentcore.runtime.model_store import get_default_model_config

            seed_model = get_default_model_config() or dict(
                DEFAULT_AGENT_JSON["model"]
            )
            config = {
                "model": seed_model,
                "tools": dict(DEFAULT_AGENT_JSON["tools"]),
                "settings": dict(DEFAULT_AGENT_JSON["settings"]),
            }
            if self.agent_id == "default":
                config["settings"]["enable_subagents"] = True
            # New agents get structured todo/plan support out of the box
            # (write_todos tool via ``settings.enable_planning``).
            config["settings"]["enable_planning"] = True
            try:
                _atomic_write_json(path, config)
                logger.info(
                    "Workspace[%s]: created default %s",
                    self.workspace_id,
                    AGENT_JSON_NAME,
                )
            except OSError as exc:
                logger.warning(
                    "Workspace[%s]: failed to create %s: %s",
                    self.workspace_id,
                    AGENT_JSON_NAME,
                    exc,
                )
            return

        # Existing file — repair a missing/incomplete model configuration.
        raw = _read_json(path)
        if not isinstance(raw, dict):
            try:
                _atomic_write_json(path, DEFAULT_AGENT_JSON)
                logger.info(
                    "Workspace[%s]: repaired unreadable %s with defaults",
                    self.workspace_id,
                    AGENT_JSON_NAME,
                )
            except OSError as exc:
                logger.warning(
                    "Workspace[%s]: failed to repair %s: %s",
                    self.workspace_id,
                    AGENT_JSON_NAME,
                    exc,
                )
            return

        model = raw.get("model")
        changed = False
        default_model = DEFAULT_AGENT_JSON.get("model", {})
        if not isinstance(model, dict):
            raw["model"] = dict(default_model)
            changed = True
        else:
            for field, default_value in default_model.items():
                value = model.get(field)
                if not (isinstance(value, str) and value.strip()):
                    model[field] = default_value
                    changed = True

        # The seeded ``default`` agent enables subagents by default —
        # but never overwrite a value the user already set (true/false).
        if self.agent_id == "default":
            settings = raw.get("settings")
            if not isinstance(settings, dict):
                settings = {}
                raw["settings"] = settings
            if "enable_subagents" not in settings:
                settings["enable_subagents"] = True
                changed = True

        if changed:
            try:
                _atomic_write_json(path, raw)
                logger.info(
                    "Workspace[%s]: backfilled incomplete model config "
                    "in %s",
                    self.workspace_id,
                    AGENT_JSON_NAME,
                )
            except OSError as exc:
                logger.warning(
                    "Workspace[%s]: failed to update %s: %s",
                    self.workspace_id,
                    AGENT_JSON_NAME,
                    exc,
                )

    def read_agent_config(self) -> dict[str, Any]:
        """Return the agent configuration from ``agent.json``.

        Missing keys are backfilled from :data:`DEFAULT_AGENT_JSON` so
        callers can always rely on the full shape.  When the file is
        missing or unreadable the defaults are returned as-is.
        """
        path = self.workspace_dir / AGENT_JSON_NAME
        raw = _read_json(path)
        if not isinstance(raw, dict):
            return {**DEFAULT_AGENT_JSON}
        config: dict[str, Any] = dict(DEFAULT_AGENT_JSON)
        config.update(raw)
        # Merge nested dicts one level deep (model / tools / skills / settings).
        for key in ("model", "tools", "skills", "settings"):
            merged = dict(DEFAULT_AGENT_JSON.get(key, {}))
            value = raw.get(key)
            if isinstance(value, dict):
                merged.update(value)
            config[key] = merged
        return config

    def write_agent_config(self, config: dict[str, Any]) -> None:
        """Persist *config* to ``agent.json`` (atomic write).

        The supplied dict is merged over :data:`DEFAULT_AGENT_JSON` so the
        stored file always carries the complete shape.
        """
        merged: dict[str, Any] = dict(DEFAULT_AGENT_JSON)
        for key, value in config.items():
            if (
                isinstance(value, dict)
                and isinstance(merged.get(key), dict)
            ):
                nested = dict(merged[key])
                nested.update(value)
                merged[key] = nested
            else:
                merged[key] = value
        _atomic_write_json(self.workspace_dir / AGENT_JSON_NAME, merged)
        logger.info(
            "Workspace[%s]: agent.json updated", self.workspace_id
        )

    # -- MCP configuration (mcp.json) ---------------------------------------

    def read_mcp_config(self) -> dict[str, Any]:
        """Return the MCP server configuration from ``mcp.json``.

        An empty dict is returned when the file is missing or corrupt —
        MCP is entirely optional.
        """
        raw = _read_json(self.workspace_dir / MCP_JSON_NAME)
        if not isinstance(raw, dict):
            return {}
        return raw

    def write_mcp_config(self, config: dict[str, Any]) -> None:
        """Persist the full MCP server configuration to ``mcp.json``."""
        _atomic_write_json(self.workspace_dir / MCP_JSON_NAME, config)
        logger.info(
            "Workspace[%s]: mcp.json updated (%d server(s))",
            self.workspace_id,
            len(config),
        )

    def _ensure_default_skills_dir(self) -> None:
        """Create the ``skills/`` directory and seed an example skill.

        The example skill is only created when the ``skills/`` directory
        itself does not exist yet — user-managed skill sets are never
        touched afterwards.
        """
        skills_dir = self.workspace_dir / SKILLS_DIR_NAME
        first_creation = not skills_dir.exists()
        try:
            skills_dir.mkdir(exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Workspace[%s]: failed to create skills directory: %s",
                self.workspace_id,
                exc,
            )
            return

        if first_creation:
            example_dir = skills_dir / "example"
            try:
                example_dir.mkdir(exist_ok=True)
                (example_dir / "SKILL.md").write_text(
                    DEFAULT_EXAMPLE_SKILL, encoding="utf-8"
                )
                logger.info(
                    "Workspace[%s]: created default skills directory with "
                    "example skill",
                    self.workspace_id,
                )
            except OSError as exc:
                logger.warning(
                    "Workspace[%s]: failed to create example skill: %s",
                    self.workspace_id,
                    exc,
                )

    def count_skills(self) -> int:
        """Return the number of skills (``<skill>/SKILL.md``) present."""
        skills_dir = self.workspace_dir / SKILLS_DIR_NAME
        if not skills_dir.exists():
            return 0
        try:
            return len(list(skills_dir.glob("*/SKILL.md")))
        except OSError:
            return 0

    # -- memory directory ---------------------------------------------------

    def _ensure_memory_dir(self) -> None:
        """Create the ``memory/`` directory with default files.

        Seeds ``MEMORY.md`` and ``USER.md`` on first creation; existing
        files are never overwritten.  Also creates the ``sessions/``
        sub-directory for daily session archives.
        """
        memory_dir = self.workspace_dir / MEMORY_DIR_NAME
        try:
            memory_dir.mkdir(exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Workspace[%s]: failed to create memory directory: %s",
                self.workspace_id,
                exc,
            )
            return

        # Seed default memory files.
        for filename, default_content in (
            (MEMORY_FILE_NAME, DEFAULT_MEMORY_MD),
            (USER_FILE_NAME, DEFAULT_USER_MD),
        ):
            path = memory_dir / filename
            if not path.exists():
                try:
                    path.write_text(default_content, encoding="utf-8")
                    logger.info(
                        "Workspace[%s]: created default memory/%s",
                        self.workspace_id,
                        filename,
                    )
                except OSError as exc:
                    logger.warning(
                        "Workspace[%s]: failed to create memory/%s: %s",
                        self.workspace_id,
                        filename,
                        exc,
                    )

        # Sessions archive sub-directory.
        sessions_dir = memory_dir / SESSIONS_ARCHIVE_DIR_NAME
        try:
            sessions_dir.mkdir(exist_ok=True)
        except OSError as exc:
            logger.warning(
                "Workspace[%s]: failed to create memory/sessions directory: %s",
                self.workspace_id,
                exc,
            )

    def get_memory_dir(self) -> Path:
        """Return the path to the workspace's ``memory/`` directory."""
        return self.workspace_dir / MEMORY_DIR_NAME

    def get_sessions_archive_dir(self) -> Path:
        """Return the path to ``memory/sessions/``."""
        return self.workspace_dir / MEMORY_DIR_NAME / SESSIONS_ARCHIVE_DIR_NAME

    def read_memory_file(self, filename: str) -> str | None:
        """Return the content of a memory file, or ``None``."""
        path = self.workspace_dir / MEMORY_DIR_NAME / filename
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def append_to_memory_file(self, filename: str, content: str) -> None:
        """Append *content* to a memory file (creating it if needed)."""
        path = self.workspace_dir / MEMORY_DIR_NAME / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)

    def archive_session_messages(
        self,
        date_str: str,
        agent_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        """Append *messages* to the daily session archive markdown file.

        The archive lives at ``memory/sessions/YYYY-MM-DD.md`` and is
        append-only — each call adds a new section with a timestamp header.
        """
        archive_dir = self.get_sessions_archive_dir()
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{date_str}.md"

        lines: list[str] = []
        if not archive_path.exists():
            lines.append(f"# Session Archive — {date_str}\n\n")

        lines.append(f"## Agent: {agent_id}\n\n")
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")
            lines.append(f"**[{role}]** _{ts}_\n\n{content}\n\n---\n\n")

        with open(archive_path, "a", encoding="utf-8") as f:
            f.write("".join(lines))

    # -- query helpers ------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return a summary of the workspace's current state."""
        return {
            "workspace_id": self.workspace_id,
            "agent_id": self.agent_id,
            "initialized": self._initialized,
            "session_count": len(self.chat_manager.sessions),
            "kernel_files": sorted(self._kernel_files.keys()),
            "skills_count": self.count_skills(),
            "driver_count": len(self.driver_manager.drivers),
            "plugin_count": len(self.plugin_registry.plugins),
            "workspace_dir": str(self.workspace_dir),
        }

    # -- dunder -------------------------------------------------------------

    def __repr__(self) -> str:
        status = "initialized" if self._initialized else "not initialized"
        return (
            f"Workspace(id={self.workspace_id!r}, "
            f"agent={self.agent_id!r}, "
            f"status={status})"
        )
