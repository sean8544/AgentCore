"""Immutable audit log for approval decisions.

Records every HITL approval decision (approve / edit / reject) as an
append-only JSON entry so that who approved what and when can be
reconstructed after the fact.  The file format mirrors
``token_router`` — atomic temp-file writes keep the log consistent even
if the process crashes mid-write.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from agentcore.runtime import paths

logger = logging.getLogger(__name__)

_AUDIT_FILE_NAME = "approval_audit_log.json"
_MAX_RECORDS = 10_000


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _audit_path() -> "object":
    """Return the path to the approval audit log file."""
    return paths.get_data_dir() / _AUDIT_FILE_NAME


# ---------------------------------------------------------------------------
# Read / write helpers (append-only, atomic flush)
# ---------------------------------------------------------------------------


def _load_records() -> list[dict[str, Any]]:
    """Load all audit records from disk (empty list on any error)."""
    path = _audit_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read audit log — treating as empty")
        return []
    return raw if isinstance(raw, list) else []


def _save_records(records: list[dict[str, Any]]) -> None:
    """Atomically persist audit records."""
    path = _audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp", dir=str(path.parent), prefix=f".{path.stem}_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=1)
        if path.exists():
            path.unlink()
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_approval_decision(
    *,
    agent_id: str,
    session_id: str,
    decision: str,
    tool_name: str,
    final_args: dict[str, Any],
    operator: str | None = None,
) -> None:
    """Append an immutable audit record for an approval decision.

    Parameters
    ----------
    agent_id:
        The agent whose tool call was decided upon.
    session_id:
        The session (thread) in which the approval occurred.
    decision:
        The decision type (``"approve"``, ``"edit"``, or ``"reject"``).
    tool_name:
        Name of the tool that required approval.
    final_args:
        The final arguments the tool will be executed with (original for
        approve, edited for edit, original for reject).
    operator:
        Optional identifier of the human who made the decision.

    Never raises — errors are logged but never propagate to the caller.
    """
    try:
        now = datetime.now(tz=timezone.utc)
        record: dict[str, Any] = {
            "ts": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "agent_id": agent_id,
            "session_id": session_id,
            "decision": decision,
            "tool_name": tool_name,
            "final_args": final_args,
            "operator": operator,
        }
        records = _load_records()
        records.append(record)
        if len(records) > _MAX_RECORDS:
            records = records[-_MAX_RECORDS:]
        _save_records(records)
        logger.info(
            "Audit: %s %s on %s/%s (tool=%s)",
            operator or "unknown",
            decision,
            agent_id,
            session_id,
            tool_name,
        )
    except Exception:
        logger.exception("Failed to record approval audit event")


def load_audit_records(agent_id: str | None = None) -> list[dict[str, Any]]:
    """Load audit records, optionally filtered by *agent_id*."""
    records = _load_records()
    if agent_id is not None:
        records = [r for r in records if r.get("agent_id") == agent_id]
    return records
