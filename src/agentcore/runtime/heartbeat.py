# -*- coding: utf-8 -*-
"""Scheduled heartbeat: run the agent with HEARTBEAT.md as query at interval.

Port of QwenPaw's ``app/crons/heartbeat.py`` concept, adapted to the
AgentCore runtime:

* Configuration lives in the per-agent ``agent.json`` (top-level
  ``heartbeat`` section): ``enabled`` / ``every`` / ``timeout_seconds`` /
  ``active_hours``.
* Each run reads the workspace's ``HEARTBEAT.md`` checklist and feeds it
  to the agent as a user message on a dedicated ``heartbeat-{agent_id}``
  thread (the checkpointer keeps memory across beats).
* A missing or empty ``HEARTBEAT.md`` — and being outside the configured
  active hours — silently skips the run (QwenPaw's safety valves).

AgentCore has no channel system, so results simply land in the heartbeat
session (visible on the Sessions / Chat pages).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from apscheduler.triggers.interval import IntervalTrigger

from agentcore.runtime.agent_ids import known_agent_ids
from agentcore.runtime.workspace import HEARTBEAT_MD_NAME

if TYPE_CHECKING:  # pragma: no cover
    from agentcore.runtime.cron_manager import CronManager

logger = logging.getLogger(__name__)

#: Default heartbeat configuration (off until the user opts in).
DEFAULT_HEARTBEAT: dict[str, Any] = {
    "enabled": False,
    "every": "30m",
    "timeout_seconds": 600,
    "active_hours": None,
}

#: Upper bound for one heartbeat execution (seconds).
MAX_TIMEOUT_SECONDS = 3600

# Pattern for "30m", "1h", "2h30m", "90s" (port of QwenPaw).
_EVERY_PATTERN = re.compile(
    r"^(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?$",
    re.IGNORECASE,
)

# 5-field cron detection — recognised so PUT can reject it with a clear
# message (cron expressions are not supported yet).
_CRON_FIELD_PATTERN = re.compile(r"^[\d\*\-/,]+$")
_DOW_NAMED = "(?:mon|tue|wed|thu|fri|sat|sun)"
_DOW_FIELD_PATTERN = re.compile(
    r"^(?:[\d\*\-/,]+|" + _DOW_NAMED + r"(?:-" + _DOW_NAMED + r")?(/[\d]+)?)$",
    re.IGNORECASE,
)


def heartbeat_session_id(agent_id: str) -> str:
    """Dedicated session / LangGraph thread for an agent's heartbeat runs."""
    return f"heartbeat-{agent_id}"


def is_cron_expression(every: str) -> bool:
    """Return True when *every* looks like a 5-field cron expression."""
    parts = (every or "").strip().split()
    if len(parts) != 5:
        return False
    if not all(_CRON_FIELD_PATTERN.match(p) for p in parts[:4]):
        return False
    return bool(_DOW_FIELD_PATTERN.match(parts[4]))


def parse_heartbeat_every(every: str) -> int:
    """Parse an interval string (``30m`` / ``1h`` / ``2h30m`` / ``90s``).

    Returns total seconds; falls back to 30 minutes on invalid input
    (port of QwenPaw's ``parse_heartbeat_every``).  Cron expressions
    must be detected via :func:`is_cron_expression` *before* calling.
    """
    every = (every or "").strip()
    if not every:
        return 30 * 60
    m = _EVERY_PATTERN.match(every)
    if not m:
        logger.warning("heartbeat every=%r invalid, using 30m", every)
        return 30 * 60
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    total = hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        return 30 * 60
    return total


def in_active_hours(active_hours: Any, now: datetime | None = None) -> bool:
    """True when the current local time falls within [start, end].

    Supports wrap-around windows (e.g. 22:00–06:00).  Missing or
    malformed configuration means "always active" (QwenPaw behaviour).
    """
    if not isinstance(active_hours, dict):
        return True
    start_s = str(active_hours.get("start") or "").strip()
    end_s = str(active_hours.get("end") or "").strip()
    if not start_s or not end_s:
        return True

    def _parse(value: str) -> tuple[int, int] | None:
        parts = value.split(":")
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return hour, minute

    start = _parse(start_s)
    end = _parse(end_s)
    if start is None or end is None:
        return True

    current = (now or datetime.now()).time()
    start_t = datetime(2000, 1, 1, start[0], start[1]).time()
    end_t = datetime(2000, 1, 1, end[0], end[1]).time()
    if start_t <= end_t:
        return start_t <= current <= end_t
    return current >= start_t or current <= end_t


def normalize_heartbeat_config(raw: Any) -> dict[str, Any]:
    """Validate and normalise a raw ``heartbeat`` section.

    Raises :class:`ValueError` with a user-readable message on invalid
    input (the router maps these to HTTP 400).
    """
    raw = raw if isinstance(raw, dict) else {}
    config: dict[str, Any] = dict(DEFAULT_HEARTBEAT)

    if "enabled" in raw:
        config["enabled"] = bool(raw["enabled"])

    if "every" in raw:
        every = str(raw["every"] or "").strip()
        if not every:
            raise ValueError("'every' must not be empty")
        if is_cron_expression(every):
            raise ValueError(
                "Cron expressions are not supported yet — use an "
                "interval like '30m', '1h' or '2h30m'"
            )
        if not _EVERY_PATTERN.match(every):
            raise ValueError(
                f"Invalid interval {every!r} — expected e.g. '30m', "
                "'1h' or '2h30m'"
            )
        config["every"] = every

    if "timeout_seconds" in raw:
        try:
            timeout = int(raw["timeout_seconds"])
        except (TypeError, ValueError):
            raise ValueError("'timeout_seconds' must be an integer") from None
        if not (1 <= timeout <= MAX_TIMEOUT_SECONDS):
            raise ValueError(
                f"'timeout_seconds' must be between 1 and {MAX_TIMEOUT_SECONDS}"
            )
        config["timeout_seconds"] = timeout

    if "active_hours" in raw:
        ah = raw["active_hours"]
        if ah is None:
            config["active_hours"] = None
        elif isinstance(ah, dict):
            start = str(ah.get("start") or "").strip()
            end = str(ah.get("end") or "").strip()
            if (start and not end) or (end and not start):
                raise ValueError(
                    "'active_hours' requires both 'start' and 'end'"
                )
            if start or end:
                for label, value in (("start", start), ("end", end)):
                    parts = value.split(":")
                    if len(parts) > 2:
                        raise ValueError(
                            f"active_hours.{label} must look like 'HH:MM'"
                        )
                    try:
                        hour = int(parts[0])
                        minute = int(parts[1]) if len(parts) > 1 else 0
                    except ValueError:
                        raise ValueError(
                            f"active_hours.{label} must look like 'HH:MM'"
                        ) from None
                    if not (0 <= hour <= 23 and 0 <= minute <= 59):
                        raise ValueError(
                            f"active_hours.{label} is out of range"
                        )
                config["active_hours"] = {"start": start, "end": end}
            else:
                config["active_hours"] = None
        else:
            raise ValueError("'active_hours' must be an object or null")

    return config


def get_heartbeat_config(workspace: Any) -> dict[str, Any]:
    """Return the agent's effective heartbeat config (with defaults)."""
    try:
        agent_config = workspace.read_agent_config()
    except Exception:
        logger.exception("Failed to read agent config for heartbeat")
        return dict(DEFAULT_HEARTBEAT)
    raw = agent_config.get("heartbeat")
    try:
        return normalize_heartbeat_config(raw)
    except ValueError:
        # A hand-edited invalid config must never break startup — fall
        # back to defaults (disabled) and log once.
        logger.warning("Invalid heartbeat config — using defaults")
        return dict(DEFAULT_HEARTBEAT)


def save_heartbeat_config(
    workspace: Any,
    config: dict[str, Any],
    last_run: dict[str, Any] | None = None,
) -> None:
    """Persist the heartbeat section into ``agent.json``.

    Other sections of ``agent.json`` are preserved (read-modify-write).
    """
    agent_config = workspace.read_agent_config()
    section = dict(config)
    existing = agent_config.get("heartbeat")
    if isinstance(existing, dict) and isinstance(existing.get("last_run"), dict):
        section.setdefault("last_run", existing["last_run"])
    if last_run is not None:
        section["last_run"] = last_run
    agent_config["heartbeat"] = section
    workspace.write_agent_config(agent_config)


def _read_query_text(workspace: Any) -> str:
    """Read the workspace's ``HEARTBEAT.md`` content (empty when absent)."""
    path = workspace.workspace_dir / HEARTBEAT_MD_NAME
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        logger.warning("Failed to read %s", path)
        return ""


# ---------------------------------------------------------------------------
# Scheduling — heartbeat jobs run as CronManager *internal* jobs
# (``_internal:heartbeat:{agent_id}``): they share the scheduler's misfire
# grace, keepalive and execution history but stay out of the user-facing
# cron job list.  The callback re-reads ``agent.json`` + ``HEARTBEAT.md``
# on every beat, so editing either file never requires re-registration.
# ---------------------------------------------------------------------------

HEARTBEAT_JOB_PREFIX = "heartbeat:"


def heartbeat_job_id(agent_id: str) -> str:
    """Internal cron job id for an agent's heartbeat (prefix added later)."""
    return HEARTBEAT_JOB_PREFIX + agent_id


async def _heartbeat_beat(state: Any, agent_id: str) -> dict[str, Any]:
    """One scheduled beat: run the heartbeat and persist the run record."""
    # Zombie-resurrection guard: even if the scheduler still has this job
    # queued (registered before the delete landed), never lazy-load a
    # workspace for an agent that no longer has a persisted record —
    # ``run_heartbeat_once`` may have returned "success" from a cached
    # graph while ``get_or_create_workspace`` below would recreate the
    # on-disk directory from scratch and bring the deleted agent back.
    if agent_id not in known_agent_ids(state):
        logger.warning(
            "Heartbeat beat dropped for %s: agent has no persisted record "
            "(likely deleted between schedule and fire)",
            agent_id,
        )
        return {"status": "skipped"}

    record = await run_heartbeat_once(state, agent_id)
    if record.get("status") not in ("skipped",):
        try:
            manager = getattr(state, "agent_manager", None)
            if manager is not None:
                workspace = await manager.get_or_create_workspace(agent_id)
                save_heartbeat_config(
                    workspace,
                    get_heartbeat_config(workspace),
                    last_run=record,
                )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Failed to persist heartbeat last_run for %s", agent_id
            )
    return {"status": record.get("status", "success")}


async def sync_heartbeat_job(
    cron_manager: "CronManager", state: Any, agent_id: str
) -> str | None:
    """Reconcile the internal heartbeat job with the saved config.

    Registers / replaces the job when the config is enabled; removes it
    otherwise.  Returns the internal job id when scheduled, else ``None``.
    """
    job_id = heartbeat_job_id(agent_id)

    # 僵尸复活防御：已删除的 agent 不得被重新排程（读取配置会懒加载
    # 重建 workspace）。删除 agent 时已同步摘除任务，这里是兜底。
    if agent_id not in known_agent_ids(state):
        cron_manager.remove_internal_job(job_id)
        return None

    async def _callback() -> dict[str, Any]:
        return await _heartbeat_beat(state, agent_id)

    try:
        manager = getattr(state, "agent_manager", None)
        workspace = (
            await manager.get_or_create_workspace(agent_id)
            if manager is not None
            else None
        )
        config = (
            get_heartbeat_config(workspace)
            if workspace is not None
            else dict(DEFAULT_HEARTBEAT)
        )
    except Exception:  # pylint: disable=broad-except
        logger.exception(
            "Heartbeat config read failed for %s — unscheduling", agent_id
        )
        cron_manager.remove_internal_job(job_id)
        return None

    if not config.get("enabled"):
        cron_manager.remove_internal_job(job_id)
        return None

    interval = parse_heartbeat_every(config.get("every", "30m"))
    cron_manager.register_internal_job(
        job_id,
        _callback,
        IntervalTrigger(seconds=interval),
        misfire_grace_seconds=max(60, interval // 2),
    )
    logger.info(
        "Heartbeat scheduled for agent %s: every %ss", agent_id, interval
    )
    return job_id


async def sync_all_heartbeat_jobs(
    cron_manager: "CronManager", state: Any, agent_ids: list[str]
) -> None:
    """Startup hook: schedule heartbeat jobs for every known agent.

    Failures are logged per agent and never block startup.
    """
    for agent_id in agent_ids:
        try:
            await sync_heartbeat_job(cron_manager, state, agent_id)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Failed to schedule heartbeat for agent %s", agent_id
            )


async def run_heartbeat_once(
    state: Any,
    agent_id: str,
    *,
    timeout: int | None = None,
    check_active_hours: bool = True,
) -> dict[str, Any]:
    """Run one heartbeat for *agent_id* and return a run record.

    *state* is the owning application's ``app.state`` (store /
    agent_manager / factory / chat_state).  The run reuses exactly the
    same machinery as the chat endpoint: cached agent graph, shared
    checkpointer, session persistence.

    The returned record shape::

        {"status": "success" | "skipped" | "timeout" | "error"
                   | "pending_approval",
         "reason": str,          # for skipped runs
         "summary": str,         # reply preview
         "session_id": str,
         "at": ISO-8601 timestamp}
    """
    from agentcore.runtime import chat_router

    at = datetime.now().astimezone().isoformat()
    session_id = heartbeat_session_id(agent_id)
    record: dict[str, Any] = {"status": "success", "at": at, "session_id": session_id}

    manager = getattr(state, "agent_manager", None)
    if manager is None:
        return {**record, "status": "error", "summary": "agent_manager unavailable"}

    # 僵尸复活防御：目标 agent 已删除时跳过，避免懒加载重建 workspace。
    if agent_id not in known_agent_ids(state):
        logger.warning("Heartbeat %s skipped: agent no longer exists", agent_id)
        return {**record, "status": "skipped", "reason": "unknown_agent"}

    try:
        workspace = await manager.get_or_create_workspace(agent_id)
    except Exception as exc:
        logger.exception("Heartbeat: failed to load workspace for %s", agent_id)
        return {**record, "status": "error", "summary": repr(exc)}

    hb = get_heartbeat_config(workspace)

    if check_active_hours and not in_active_hours(hb.get("active_hours")):
        logger.debug("Heartbeat %s skipped: outside active hours", agent_id)
        return {**record, "status": "skipped", "reason": "outside_active_hours"}

    query_text = _read_query_text(workspace)
    if not query_text:
        logger.debug("Heartbeat %s skipped: no/empty HEARTBEAT.md", agent_id)
        return {**record, "status": "skipped", "reason": "no_query_file"}

    store = getattr(state, "store", None)
    factory = getattr(state, "factory", None)
    chat_state = chat_router.get_chat_state(state)

    # --- Sandbox session management ---
    # If the agent uses sandbox, ensure the session is active so the
    # heartbeat runs inside the container (not on the host).
    sandbox_backend = None
    sandbox_mgr = getattr(state, "sandbox_session_manager", None)
    if sandbox_mgr is not None:
        try:
            agent_config = workspace.read_agent_config()
            backend_cfg = (agent_config.get("settings") or {}).get("backend", {})
            if backend_cfg.get("type") == "sandbox":
                session = await sandbox_mgr.get_or_create(agent_id, backend_cfg)
                if session is not None:
                    sandbox_backend = session.backend
                    logger.info(
                        "Heartbeat %s: sandbox session ready (id=%s, status=%s)",
                        agent_id, session.sandbox_id, session.status,
                    )
        except Exception:
            logger.exception(
                "Heartbeat %s: failed to get/create sandbox session", agent_id,
            )

    try:
        agent_graph = chat_router._resolve_agent_graph(
            agent_id,
            workspace,
            factory=factory,
            manager=manager,
            chat_state=chat_state,
            sandbox_backend=sandbox_backend,
        )
    except Exception as exc:
        logger.exception("Heartbeat %s: graph resolution failed", agent_id)
        return {**record, "status": "error", "summary": repr(exc)}

    # Track the heartbeat thread so graph invalidation also purges its
    # checkpointed state, and persist the query like a normal user turn.
    chat_state.threads.setdefault(agent_id, set()).add(session_id)
    if store is not None:
        chat_router._ensure_session(store, session_id, agent_id)
        chat_router._append_message(
            store, session_id, "user", query_text,
            extras={"source": "heartbeat"},
        )

    effective_timeout = timeout or int(hb.get("timeout_seconds", 600))
    try:
        from langchain_core.messages import HumanMessage

        from agentcore.runtime.agent_runtime import mark_turn_begin, mark_turn_end

        await mark_turn_begin(state, agent_id)
        try:
            result = await asyncio.wait_for(
                agent_graph.ainvoke(
                    {"messages": [HumanMessage(content=query_text)]},
                    config={"configurable": {"thread_id": session_id}},
                ),
                timeout=effective_timeout,
            )
        finally:
            await mark_turn_end(state, agent_id)
    except asyncio.TimeoutError:
        logger.warning(
            "Heartbeat %s timed out after %ss", agent_id, effective_timeout
        )
        return {
            **record,
            "status": "timeout",
            "summary": f"Heartbeat timed out after {effective_timeout}s",
        }
    except Exception as exc:
        logger.exception("Heartbeat %s execution failed", agent_id)
        return {**record, "status": "error", "summary": repr(exc)}

    # HITL interrupt during a heartbeat — surface it as a status; the
    # session can still be resumed from the Chat / Sessions pages.
    interrupts = chat_router._extract_interrupts_from_result(result)
    if interrupts:
        approval_info = chat_router._build_approval_request_info(interrupts)
        if store is not None:
            chat_router._append_message(
                store, session_id, "assistant", "",
                extras={"approval_request": approval_info, "source": "heartbeat"},
            )
        return {
            **record,
            "status": "pending_approval",
            "summary": "Heartbeat paused waiting for approval",
        }

    content, tool_calls = chat_router._extract_text_from_result(result)
    content = chat_router._collapse_blank_runs(content)
    if store is not None:
        chat_router._append_message(
            store, session_id, "assistant", content, tool_calls or None,
            extras={"source": "heartbeat"},
        )

    # Best-effort pull-back: files the agent created inside the sandbox
    # are mirrored back to the workspace so they appear in the Files page.
    if sandbox_backend is not None:
        try:
            await chat_router._pull_sandbox_storage(state, agent_id, workspace)
        except Exception:
            logger.debug(
                "Heartbeat %s: sandbox pull-back failed (non-fatal)", agent_id,
            )

    return {**record, "summary": content[:500]}
