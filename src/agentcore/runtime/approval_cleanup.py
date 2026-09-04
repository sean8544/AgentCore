# -*- coding: utf-8 -*-
"""Stale background-approval cleanup for the Inbox.

Background runs (heartbeat / cron / memory consolidation) pause on HITL
approvals and persist an ``approval_request`` on their dedicated session.
Nobody may be watching those sessions for days — such requests are
*zombie* approvals that keep the Inbox badge lit forever.

This module expires approvals raised by **background sessions only**
(never plain user chats) once they have been pending for more than
:data:`APPROVAL_EXPIRY_DAYS` days:

* the requesting message loses its actionable ``approval_request``
* it is annotated ``approval_expired`` + ``approval_expired_at`` (the
  session history still shows what happened)
* ``has_pending_approval`` for that session flips to ``False``, so the
  Inbox badge and list drop the entry automatically

A daily cleanup job is registered as a CronManager *internal* job
(``_internal:approval-cleanup``), the same mechanism as the heartbeat.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:  # pragma: no cover
    from agentcore.runtime.cron_manager import CronManager

logger = logging.getLogger(__name__)

#: How long a background approval may sit undecided before it expires.
APPROVAL_EXPIRY_DAYS = 7

#: Session id prefixes owned by unattended background runs.
BACKGROUND_SESSION_PREFIXES = (
    "heartbeat-",
    "cron:",
    "memory-consolidation-",
)

#: Internal cron job id of the daily cleanup pass.
CLEANUP_JOB_ID = "approval-cleanup"


def is_background_session(session_id: str) -> bool:
    """True when *session_id* belongs to an unattended background run."""
    return any(session_id.startswith(p) for p in BACKGROUND_SESSION_PREFIXES)


def _latest_assistant_message(data: dict[str, Any]) -> dict[str, Any] | None:
    for msg in reversed(data.get("messages", [])):
        if msg.get("role") == "assistant":
            return msg
    return None


def has_expired_approval(data: dict[str, Any]) -> bool:
    """True when the latest assistant message carries an expiry marker."""
    msg = _latest_assistant_message(data)
    return bool(msg and msg.get("approval_expired"))


def _request_age_days(msg: dict[str, Any], data: dict[str, Any]) -> float:
    """Age of the approval request in days (falls back to session update)."""
    raw = msg.get("timestamp") or data.get("updated_at") or ""
    try:
        ts = datetime.fromisoformat(str(raw))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0


def expire_stale_approvals(
    app_state: Any, *, max_age_days: int = APPROVAL_EXPIRY_DAYS
) -> dict[str, int]:
    """Mark undecided background approvals older than *max_age_days* expired.

    Returns ``{"scanned": n, "expired": m}``.  Plain chat sessions are
    never touched — their approvals may still be awaiting the user.
    """
    store = getattr(app_state, "store", None)
    if store is None:
        return {"scanned": 0, "expired": 0}

    scanned = 0
    expired = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for sid, data in store.load_sessions().items():
        if not is_background_session(sid):
            continue
        scanned += 1
        msg = _latest_assistant_message(data)
        if msg is None:
            continue
        # Must be an undecided, not-yet-expired approval request.
        if not (msg.get("approval_request") and not msg.get("approval")):
            continue
        if msg.get("approval_expired"):
            continue
        if _request_age_days(msg, data) <= max_age_days:
            continue

        # Drop the actionable request (the checkpoint may be long gone,
        # so an approval card must not stay interactive) but keep the
        # trace in history via the expiry markers.
        msg.pop("approval_request", None)
        msg["approval_expired"] = True
        msg["approval_expired_at"] = now_iso
        data["updated_at"] = now_iso
        store.save_session(sid, data)
        expired += 1
        logger.info(
            "Expired stale %s approval on session %s (pending > %s days)",
            _session_kind(sid),
            sid,
            max_age_days,
        )
    return {"scanned": scanned, "expired": expired}


def _session_kind(session_id: str) -> str:
    for prefix in BACKGROUND_SESSION_PREFIXES:
        if session_id.startswith(prefix):
            return prefix.rstrip(":-")
    return "background"


async def _cleanup_beat(app_state: Any) -> dict[str, Any]:
    stats = expire_stale_approvals(app_state)
    status = "success" if stats["expired"] == 0 else "expired"
    return {"status": status, **stats}


def register_approval_cleanup_job(
    cron_manager: "CronManager", app_state: Any
) -> str:
    """Register the daily cleanup as a CronManager internal job.

    Runs at 03:07 local time; a large misfire grace absorbs long machine
    downtime.  Idempotent — calling again replaces the existing job.
    """
    job_id = f"_internal:{CLEANUP_JOB_ID}"

    async def _callback() -> dict[str, Any]:
        return await _cleanup_beat(app_state)

    cron_manager.register_internal_job(
        job_id,
        _callback,
        CronTrigger(hour=3, minute=7),
        misfire_grace_seconds=12 * 3600,
    )
    logger.info("Registered daily approval cleanup job (%s)", job_id)
    return job_id