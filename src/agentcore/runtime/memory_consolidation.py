# -*- coding: utf-8 -*-
"""Background memory consolidation (sleep-time compute).

Implements the deepagents "background consolidation" pattern: instead of
relying solely on the agent to update its memory files during a
conversation (hot path), a scheduled run reviews the daily session
archives (``memory/sessions/YYYY-MM-DD.md``, written by the chat router)
and merges durable facts into the long-term memory files
(``memory/MEMORY.md`` / ``memory/USER.md``).

Mechanics
---------
* Configuration lives in the per-agent ``agent.json`` (top-level
  ``memory`` section)::

      {"enabled": true, "consolidate_cron": "0 3 * * *",
       "retain_days": 30, "max_memory_kb": 16, "timeout_seconds": 600}

* Each run invokes the *same* cached agent graph on a dedicated
  ``memory-consolidation-{agent_id}`` thread with a fixed instruction
  prompt listing the not-yet-consolidated archive files.  The agent uses
  its ordinary file tools to merge findings into the memory files.
* A watermark file (``memory/.consolidation_state.json``) tracks which
  archives have been consumed so runs never reprocess the same content.
* After a successful run, archives older than ``retain_days`` are moved
  to ``memory/sessions/archived/`` to keep the active directory lean.

Scheduling reuses the :class:`CronManager` *internal* job mechanism
(``_internal:memory-consolidation:{agent_id}``) — the same pattern as
the heartbeat.

Note on visibility: the SDK's ``MemoryMiddleware`` loads ``memory=[]``
sources once per thread (checkpointed ``memory_contents``), so
consolidation results become effective in **new** sessions — the
documented upstream behaviour.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from apscheduler.triggers.cron import CronTrigger

from agentcore.runtime.agent_ids import known_agent_ids

if TYPE_CHECKING:  # pragma: no cover
    from agentcore.runtime.cron_manager import CronManager

logger = logging.getLogger(__name__)

#: Default memory configuration — consolidation enabled, nightly at 03:00.
DEFAULT_MEMORY_CONFIG: dict[str, Any] = {
    "enabled": True,
    "consolidate_cron": "0 3 * * *",
    "retain_days": 30,
    "max_memory_kb": 16,
    "timeout_seconds": 600,
}

#: Upper bound for one consolidation execution (seconds).
MAX_TIMEOUT_SECONDS = 3600

#: Watermark / bookkeeping file inside ``memory/``.
CONSOLIDATION_STATE_FILE_NAME = ".consolidation_state.json"

#: Sub-directory that receives archives older than ``retain_days``.
ARCHIVED_DIR_NAME = "archived"

CONSOLIDATION_JOB_PREFIX = "memory-consolidation:"

#: Daily archive files are named ``YYYY-MM-DD.md``.
_ARCHIVE_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


def consolidation_session_id(agent_id: str) -> str:
    """Dedicated session / LangGraph thread for consolidation runs."""
    return f"memory-consolidation-{agent_id}"


def consolidation_job_id(agent_id: str) -> str:
    """Internal cron job id for an agent's consolidation (prefix added later)."""
    return CONSOLIDATION_JOB_PREFIX + agent_id


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def validate_cron_expression(expr: str) -> str:
    """Normalise and validate a cron expression for ``consolidate_cron``.

    Returns the normalised 5-field expression; raises :class:`ValueError`
    with a user-readable message on invalid input.
    """
    from agentcore.runtime.cron_manager import normalize_cron_5_fields

    try:
        normalized = normalize_cron_5_fields(str(expr).strip())
    except ValueError as exc:
        raise ValueError(
            f"Invalid cron expression {expr!r} — expected 5 fields like "
            "'0 3 * * *'"
        ) from exc

    minute, hour, day, month, day_of_week = normalized.split()
    try:
        # Constructing the trigger validates every field's syntax.
        CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
        )
    except ValueError as exc:
        raise ValueError(
            f"Invalid cron expression {expr!r}: {exc}"
        ) from exc
    return normalized


def normalize_memory_config(raw: Any) -> dict[str, Any]:
    """Validate and normalise a raw ``memory`` section.

    Raises :class:`ValueError` with a user-readable message on invalid
    input (the router maps these to HTTP 400).
    """
    raw = raw if isinstance(raw, dict) else {}
    config: dict[str, Any] = dict(DEFAULT_MEMORY_CONFIG)

    if "enabled" in raw:
        config["enabled"] = bool(raw["enabled"])

    if "consolidate_cron" in raw:
        expr = str(raw["consolidate_cron"] or "").strip()
        if not expr:
            raise ValueError("'consolidate_cron' must not be empty")
        config["consolidate_cron"] = validate_cron_expression(expr)

    if "retain_days" in raw:
        try:
            retain = int(raw["retain_days"])
        except (TypeError, ValueError):
            raise ValueError("'retain_days' must be an integer") from None
        if not (1 <= retain <= 3650):
            raise ValueError("'retain_days' must be between 1 and 3650")
        config["retain_days"] = retain

    if "max_memory_kb" in raw:
        try:
            max_kb = int(raw["max_memory_kb"])
        except (TypeError, ValueError):
            raise ValueError("'max_memory_kb' must be an integer") from None
        if not (1 <= max_kb <= 1024):
            raise ValueError("'max_memory_kb' must be between 1 and 1024")
        config["max_memory_kb"] = max_kb

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

    return config


def get_memory_config(workspace: Any) -> dict[str, Any]:
    """Return the agent's effective memory config (with defaults)."""
    try:
        agent_config = workspace.read_agent_config()
    except Exception:
        logger.exception("Failed to read agent config for memory")
        return dict(DEFAULT_MEMORY_CONFIG)
    raw = agent_config.get("memory")
    try:
        return normalize_memory_config(raw)
    except ValueError:
        # A hand-edited invalid config must never break startup — fall
        # back to defaults and log once.
        logger.warning("Invalid memory config — using defaults")
        return dict(DEFAULT_MEMORY_CONFIG)


def save_memory_config(
    workspace: Any,
    config: dict[str, Any],
    last_run: dict[str, Any] | None = None,
) -> None:
    """Persist the memory section into ``agent.json``.

    Other sections of ``agent.json`` are preserved (read-modify-write).
    """
    agent_config = workspace.read_agent_config()
    section = dict(config)
    existing = agent_config.get("memory")
    if isinstance(existing, dict) and isinstance(existing.get("last_run"), dict):
        section.setdefault("last_run", existing["last_run"])
    if last_run is not None:
        section["last_run"] = last_run
    agent_config["memory"] = section
    workspace.write_agent_config(agent_config)


# ---------------------------------------------------------------------------
# Consolidation state (watermark) and archive bookkeeping
# ---------------------------------------------------------------------------


def _state_path(workspace: Any):
    return workspace.get_memory_dir() / CONSOLIDATION_STATE_FILE_NAME


def read_consolidation_state(workspace: Any) -> dict[str, Any]:
    """Read the watermark file; missing / corrupt files mean "fresh"."""
    path = _state_path(workspace)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        logger.warning("Corrupt consolidation state file: %s", path)
        return {}


def write_consolidation_state(workspace: Any, state: dict[str, Any]) -> None:
    path = _state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_session_archives(
    workspace: Any, *, archived: bool = False
) -> list[dict[str, Any]]:
    """List daily archive files (active or archived) with metadata."""
    base = workspace.get_sessions_archive_dir()
    directory = base / ARCHIVED_DIR_NAME if archived else base
    if not directory.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        if not _ARCHIVE_NAME_RE.match(path.name):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            }
        )
    return entries


def pending_archive_files(
    workspace: Any, state: dict[str, Any]
) -> list[Any]:
    """Return archive files modified after the consolidation watermark.

    The watermark (``last_consolidated_at``) is compared against each
    file's mtime, which correctly handles same-day appends: an archive
    extended after the last run is picked up again.
    """
    watermark_raw = state.get("last_consolidated_at")
    watermark_ts: float | None = None
    if watermark_raw:
        try:
            watermark_ts = datetime.fromisoformat(str(watermark_raw)).timestamp()
        except ValueError:
            watermark_ts = None

    base = workspace.get_sessions_archive_dir()
    if not base.is_dir():
        return []
    pending = []
    for path in sorted(base.glob("*.md")):
        if not _ARCHIVE_NAME_RE.match(path.name):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if watermark_ts is None or mtime > watermark_ts:
            pending.append(path)
    return pending


def move_expired_archives(workspace: Any, retain_days: int) -> list[str]:
    """Move archives older than *retain_days* into ``sessions/archived/``.

    Returns the names of moved files.  Filename dates are used (not
    mtimes) so the rule is stable across copies/repairs.
    """
    base = workspace.get_sessions_archive_dir()
    if not base.is_dir():
        return []
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=retain_days)).date()
    target_dir = base / ARCHIVED_DIR_NAME
    moved: list[str] = []
    for path in sorted(base.glob("*.md")):
        match = _ARCHIVE_NAME_RE.match(path.name)
        if not match:
            continue
        try:
            file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date >= cutoff:
            continue
        try:
            target_dir.mkdir(exist_ok=True)
            shutil.move(str(path), str(target_dir / path.name))
            moved.append(path.name)
        except OSError as exc:
            logger.warning("Failed to archive %s: %s", path.name, exc)
    if moved:
        logger.info("Moved %d expired session archive(s)", len(moved))
    return moved


# ---------------------------------------------------------------------------
# Consolidation run
# ---------------------------------------------------------------------------


def _build_prompt(pending_names: list[str], max_memory_kb: int) -> str:
    """Assemble the instruction prompt for one consolidation run."""
    listing = "\n".join(f"- /memory/sessions/{name}" for name in pending_names)
    return (
        "这是一次后台记忆巩固任务。请按以下步骤执行：\n\n"
        "1. 使用 read_file 逐个阅读下列新增的会话归档文件：\n"
        f"{listing}\n"
        "2. 从归档中提取**长期有效**的信息：稳定的事实、用户偏好、重要决定、"
        "经验教训。忽略一次性任务、临时状态与寒暄。\n"
        "3. 先 read_file 现有的 /memory/MEMORY.md 与 /memory/USER.md，"
        "再把新内容合并进去：\n"
        "   - 与用户相关的偏好、习惯 → 用 edit_file 更新 /memory/USER.md\n"
        "   - 其他事实与知识 → 用 edit_file 更新 /memory/MEMORY.md\n"
        "   合并时去除重复与已过时的条目，保持条目简洁。\n"
        f"4. 若 /memory/MEMORY.md 合并后超过 {max_memory_kb}KB，必须压缩："
        "归纳相似条目、删除低价值内容。\n"
        "5. 严禁记录任何 API key、口令、令牌等凭据信息。\n"
        "6. 完成后用一两句话总结本次巩固做了什么。\n"
    )


async def run_consolidation_once(
    state: Any,
    agent_id: str,
    *,
    force: bool = False,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Run one memory consolidation for *agent_id* and return a record.

    *state* is the owning application's ``app.state``.  The run reuses the
    same machinery as chat / heartbeat: cached agent graph, shared
    checkpointer, session persistence.

    ``force=True`` (manual trigger) runs even when consolidation is
    disabled or no new archives exist.  Scheduled runs skip silently in
    both cases.

    The returned record shape::

        {"status": "success" | "skipped" | "timeout" | "error"
                   | "pending_approval",
         "reason": str,          # for skipped runs
         "summary": str,
         "session_id": str,
         "consolidated_files": list[str],
         "archived_files": list[str],
         "at": ISO-8601 timestamp}
    """
    from agentcore.runtime import chat_router

    at = datetime.now().astimezone().isoformat()
    session_id = consolidation_session_id(agent_id)
    record: dict[str, Any] = {
        "status": "success",
        "at": at,
        "session_id": session_id,
        "consolidated_files": [],
        "archived_files": [],
    }

    manager = getattr(state, "agent_manager", None)
    if manager is None:
        return {**record, "status": "error", "summary": "agent_manager unavailable"}

    # Zombie-resurrection guard: the scheduler may still fire a stale job
    # between ``delete_agent`` removing the persisted record and the
    # ``remove_internal_job`` call landing.  Never lazy-load a workspace
    # for an agent that no longer exists — the load itself would
    # recreate the on-disk directory and undo the delete.
    if agent_id not in known_agent_ids(state):
        logger.warning(
            "Consolidation skipped for %s: agent has no persisted record",
            agent_id,
        )
        return {**record, "status": "skipped", "reason": "unknown_agent"}

    try:
        workspace = await manager.get_or_create_workspace(agent_id)
    except Exception as exc:
        logger.exception(
            "Consolidation: failed to load workspace for %s", agent_id
        )
        return {**record, "status": "error", "summary": repr(exc)}

    config = get_memory_config(workspace)
    if not force and not config.get("enabled"):
        return {**record, "status": "skipped", "reason": "disabled"}

    cons_state = read_consolidation_state(workspace)
    pending = pending_archive_files(workspace, cons_state)
    if not pending and not force:
        logger.debug("Consolidation %s skipped: no new archives", agent_id)
        return {**record, "status": "skipped", "reason": "no_new_archives"}

    pending_names = [p.name for p in pending]
    prompt = _build_prompt(pending_names, int(config.get("max_memory_kb", 16)))

    store = getattr(state, "store", None)
    factory = getattr(state, "factory", None)
    chat_state = chat_router.get_chat_state(state)

    try:
        agent_graph = chat_router._resolve_agent_graph(
            agent_id,
            workspace,
            factory=factory,
            manager=manager,
            chat_state=chat_state,
        )
    except Exception as exc:
        logger.exception("Consolidation %s: graph resolution failed", agent_id)
        return {**record, "status": "error", "summary": repr(exc)}

    # Track the consolidation thread so graph invalidation also purges
    # its checkpointed state, and persist the prompt like a normal turn.
    chat_state.threads.setdefault(agent_id, set()).add(session_id)
    if store is not None:
        chat_router._ensure_session(store, session_id, agent_id)
        chat_router._append_message(
            store, session_id, "user", prompt,
            extras={"source": "memory-consolidation"},
        )

    # Watermark captured *before* the run: files appended while the agent
    # works remain pending for the next consolidation.
    run_watermark = datetime.now(tz=timezone.utc)

    effective_timeout = timeout or int(config.get("timeout_seconds", 600))
    try:
        from langchain_core.messages import HumanMessage

        result = await asyncio.wait_for(
            agent_graph.ainvoke(
                {"messages": [HumanMessage(content=prompt)]},
                config={"configurable": {"thread_id": session_id}},
            ),
            timeout=effective_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Consolidation %s timed out after %ss", agent_id, effective_timeout
        )
        record = {
            **record,
            "status": "timeout",
            "summary": f"Consolidation timed out after {effective_timeout}s",
        }
        _persist_last_run(workspace, record)
        return record
    except Exception as exc:
        logger.exception("Consolidation %s execution failed", agent_id)
        record = {**record, "status": "error", "summary": repr(exc)}
        _persist_last_run(workspace, record)
        return record

    # HITL interrupt during consolidation — surface it; the watermark is
    # NOT advanced so the same archives are retried next time.
    interrupts = chat_router._extract_interrupts_from_result(result)
    if interrupts:
        approval_info = chat_router._build_approval_request_info(interrupts)
        if store is not None:
            chat_router._append_message(
                store, session_id, "assistant", "",
                extras={
                    "approval_request": approval_info,
                    "source": "memory-consolidation",
                },
            )
        record = {
            **record,
            "status": "pending_approval",
            "summary": "Consolidation paused waiting for approval",
        }
        _persist_last_run(workspace, record)
        return record

    content, tool_calls = chat_router._extract_text_from_result(result)
    content = chat_router._collapse_blank_runs(content)
    if store is not None:
        chat_router._append_message(
            store, session_id, "assistant", content, tool_calls or None,
            extras={"source": "memory-consolidation"},
        )

    # Success — advance the watermark and prune expired archives.
    try:
        write_consolidation_state(
            workspace,
            {
                "last_consolidated_at": run_watermark.isoformat(),
                "last_consolidated_files": pending_names,
            },
        )
        moved = move_expired_archives(workspace, int(config.get("retain_days", 30)))
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to update consolidation state for %s", agent_id)
        moved = []
        record = {**record, "status": "error", "summary": repr(exc)}
        _persist_last_run(workspace, record)
        return record

    record = {
        **record,
        "summary": content[:500],
        "consolidated_files": pending_names,
        "archived_files": moved,
    }
    _persist_last_run(workspace, record)
    return record


def _persist_last_run(workspace: Any, record: dict[str, Any]) -> None:
    """Best-effort persistence of the run record into ``agent.json``."""
    if record.get("status") == "skipped":
        return
    try:
        save_memory_config(workspace, get_memory_config(workspace), last_run=record)
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to persist memory last_run")


# ---------------------------------------------------------------------------
# Scheduling — consolidation runs as a CronManager *internal* job
# (``_internal:memory-consolidation:{agent_id}``), mirroring the heartbeat.
# The callback re-reads ``agent.json`` on every run, so config edits never
# require re-registration (though PUT does re-sync for promptness).
# ---------------------------------------------------------------------------


async def _consolidation_beat(state: Any, agent_id: str) -> dict[str, Any]:
    """One scheduled run; last_run persistence happens inside the run."""
    record = await run_consolidation_once(state, agent_id)
    return {"status": record.get("status", "success")}


async def sync_memory_job(
    cron_manager: "CronManager", state: Any, agent_id: str
) -> str | None:
    """Reconcile the internal consolidation job with the saved config.

    Registers / replaces the job when the config is enabled; removes it
    otherwise.  Returns the internal job id when scheduled, else ``None``.
    """
    job_id = consolidation_job_id(agent_id)

    # 僵尸复活防御：已删除的 agent 不得被重新排程（读取配置会懒加载
    # 重建 workspace）。删除 agent 时已同步摘除任务，这里是最后防线。
    if agent_id not in known_agent_ids(state):
        cron_manager.remove_internal_job(job_id)
        return None

    async def _callback() -> dict[str, Any]:
        return await _consolidation_beat(state, agent_id)

    try:
        manager = getattr(state, "agent_manager", None)
        workspace = (
            await manager.get_or_create_workspace(agent_id)
            if manager is not None
            else None
        )
        config = (
            get_memory_config(workspace)
            if workspace is not None
            else dict(DEFAULT_MEMORY_CONFIG)
        )
    except Exception:  # pylint: disable=broad-except
        logger.exception(
            "Memory config read failed for %s — unscheduling", agent_id
        )
        cron_manager.remove_internal_job(job_id)
        return None

    if not config.get("enabled"):
        cron_manager.remove_internal_job(job_id)
        return None

    cron_expr = config.get("consolidate_cron", "0 3 * * *")
    try:
        cron_expr = validate_cron_expression(cron_expr)
    except ValueError:
        logger.warning(
            "Invalid consolidate_cron %r for %s — unscheduling",
            cron_expr,
            agent_id,
        )
        cron_manager.remove_internal_job(job_id)
        return None

    minute, hour, day, month, day_of_week = cron_expr.split()
    cron_manager.register_internal_job(
        job_id,
        _callback,
        CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
        ),
        misfire_grace_seconds=3600,
    )
    logger.info(
        "Memory consolidation scheduled for agent %s: %s", agent_id, cron_expr
    )
    return job_id


async def sync_all_memory_jobs(
    cron_manager: "CronManager", state: Any, agent_ids: list[str]
) -> None:
    """Startup hook: schedule consolidation jobs for every known agent.

    Failures are logged per agent and never block startup.
    """
    for agent_id in agent_ids:
        try:
            await sync_memory_job(cron_manager, state, agent_id)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Failed to schedule memory consolidation for agent %s",
                agent_id,
            )
