# -*- coding: utf-8 -*-
"""Background-approval expiry tests (Inbox stale cleanup).

Covers:
- background session detection by id prefix
- expiry marker readback (``has_expired_approval``)
- ``expire_stale_approvals``: age cut-off, chat sessions untouched,
  already-decided / already-expired requests untouched
- chat router ``/sessions`` integration: expired approvals are excluded
  from ``has_pending_approval`` and surfaced via ``has_expired_approval``
- CronManager internal job registration + lifespan wiring
"""

from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agentcore.api import app
from agentcore.runtime import approval_cleanup as ac
from agentcore.runtime import chat_router
from agentcore.runtime.cron_manager import CronManager


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _session(
    sid: str,
    *,
    pending_days_ago: int | None = None,
    decided: bool = False,
    expired: bool = False,
) -> dict:
    """Build a session dict: user tick + assistant approval request."""
    msgs = [{"role": "user", "content": "tick", "timestamp": _iso(10)}]
    assistant: dict = {
        "role": "assistant",
        "content": "needs approval",
        "timestamp": _iso(pending_days_ago or 0),
    }
    if pending_days_ago is not None:
        assistant["approval_request"] = {"actions": [{"name": "run"}]}
    if decided:
        assistant["approval"] = {"decision": "approve", "timestamp": _iso(1)}
    if expired:
        assistant["approval_expired"] = True
        assistant["approval_expired_at"] = _iso(1)
    msgs.append(assistant)
    return {"session_id": sid, "agent_id": "default", "messages": msgs}


class _FakeStore:
    def __init__(self, sessions: dict[str, dict]) -> None:
        self._sessions = sessions

    def load_sessions(self) -> dict[str, dict]:
        return self._sessions

    def save_session(self, sid: str, data: dict) -> None:
        self._sessions[sid] = data


def _app_state(sessions: dict[str, dict]) -> types.SimpleNamespace:
    return types.SimpleNamespace(store=_FakeStore(sessions))


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client running inside an isolated (temporary) data directory."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Session classification
# ---------------------------------------------------------------------------


def test_is_background_session_prefixes() -> None:
    assert ac.is_background_session("heartbeat-default") is True
    assert ac.is_background_session("cron:nightly") is True
    assert ac.is_background_session("memory-consolidation-default") is True
    assert ac.is_background_session("a6f6bb8dae4740b49718463c7634fb2a") is False
    assert ac.is_background_session("chat-session") is False


def test_has_expired_approval_reads_latest_marker() -> None:
    expired = _session("heartbeat-d", pending_days_ago=8, expired=True)
    assert ac.has_expired_approval(expired) is True
    pending = _session("heartbeat-d", pending_days_ago=8)
    assert ac.has_expired_approval(pending) is False
    decided = _session("heartbeat-d", pending_days_ago=8, decided=True)
    assert ac.has_expired_approval(decided) is False
    no_messages = {"session_id": "heartbeat-d", "messages": []}
    assert ac.has_expired_approval(no_messages) is False


# ---------------------------------------------------------------------------
# expire_stale_approvals
# ---------------------------------------------------------------------------


def test_missing_store_is_noop() -> None:
    assert ac.expire_stale_approvals(types.SimpleNamespace(store=None)) == {
        "scanned": 0,
        "expired": 0,
    }


def test_expires_stale_background_approval() -> None:
    stale = _session("heartbeat-default", pending_days_ago=8)
    state = _app_state({"heartbeat-default": stale})

    assert ac.expire_stale_approvals(state) == {"scanned": 1, "expired": 1}

    latest = stale["messages"][-1]
    assert "approval_request" not in latest
    assert latest["approval_expired"] is True
    assert latest["approval_expired_at"]
    # Session was persisted back with a fresh update timestamp that parses.
    assert stale.get("updated_at")
    datetime.fromisoformat(stale["updated_at"])


def test_keeps_approval_within_grace_period() -> None:
    fresh = _session("heartbeat-default", pending_days_ago=6)
    state = _app_state({"heartbeat-default": fresh})

    assert ac.expire_stale_approvals(state) == {"scanned": 1, "expired": 0}
    assert fresh["messages"][-1]["approval_request"]  # still actionable


def test_never_touches_plain_chat_sessions() -> None:
    chat = _session("a6f6bb8dae4740b49718463c7634fb2a", pending_days_ago=30)
    state = _app_state({"a6f6bb8dae4740b49718463c7634fb2a": chat})

    assert ac.expire_stale_approvals(state) == {"scanned": 0, "expired": 0}
    assert chat["messages"][-1]["approval_request"]


def test_expires_all_background_kinds() -> None:
    sessions = {
        "heartbeat-default": _session("heartbeat-default", pending_days_ago=9),
        "cron:nightly": _session("cron:nightly", pending_days_ago=9),
        "memory-consolidation-default": _session(
            "memory-consolidation-default", pending_days_ago=9
        ),
    }
    assert ac.expire_stale_approvals(_app_state(sessions)) == {
        "scanned": 3,
        "expired": 3,
    }


def test_skips_already_decided_approvals() -> None:
    decided = _session("heartbeat-default", pending_days_ago=9, decided=True)
    state = _app_state({"heartbeat-default": decided})

    assert ac.expire_stale_approvals(state) == {"scanned": 1, "expired": 0}
    assert not decided["messages"][-1].get("approval_expired")


def test_skips_already_expired_requests() -> None:
    already = _session("heartbeat-default", pending_days_ago=9, expired=True)
    state = _app_state({"heartbeat-default": already})

    assert ac.expire_stale_approvals(state) == {"scanned": 1, "expired": 0}


def test_skips_session_without_actionable_latest_request() -> None:
    odd = _session("heartbeat-default", pending_days_ago=9)
    # A newer assistant message without an approval request wins: the
    # outdated request below it is not considered actionable any more.
    odd["messages"].append(
        {"role": "assistant", "content": "follow-up", "timestamp": _iso(0)}
    )
    state = _app_state({"heartbeat-default": odd})

    assert ac.expire_stale_approvals(state) == {"scanned": 1, "expired": 0}


# ---------------------------------------------------------------------------
# chat_router integration
# ---------------------------------------------------------------------------


def test_pending_approval_ignores_expired_marker() -> None:
    expired = _session("heartbeat-default", pending_days_ago=8, expired=True)
    assert chat_router._session_has_pending_approval(expired) is False
    pending = _session("heartbeat-default", pending_days_ago=1)
    assert chat_router._session_has_pending_approval(pending) is True


def test_session_list_flags_expired_approval(client) -> None:
    store = client.app.state.store
    sid = "heartbeat-default"
    store.save_session(sid, _session(sid, pending_days_ago=8, expired=True))

    entries = client.get("/api/chat/sessions").json()
    entry = next(s for s in entries if s["session_id"] == sid)
    assert entry["has_expired_approval"] is True
    assert entry["has_pending_approval"] is False


def test_cleanup_pass_visible_in_session_list(client) -> None:
    store = client.app.state.store
    sid = "cron:nightly"
    store.save_session(sid, _session(sid, pending_days_ago=8))

    # Run the same pass the daily job invokes.
    stats = ac.expire_stale_approvals(client.app.state)
    assert stats == {"scanned": 1, "expired": 1}

    entry = next(
        s
        for s in client.get("/api/chat/sessions").json()
        if s["session_id"] == sid
    )
    assert entry["has_pending_approval"] is False
    assert entry["has_expired_approval"] is True
    # The stored request is no longer actionable.
    assert "approval_request" not in store.load_sessions()[sid]["messages"][-1]


# ---------------------------------------------------------------------------
# CronManager job wiring
# ---------------------------------------------------------------------------


def test_register_cleanup_job() -> None:
    manager = CronManager(types.SimpleNamespace())
    job_id = ac.register_approval_cleanup_job(manager, types.SimpleNamespace())
    assert job_id == "_internal:approval-cleanup"
    assert manager.has_internal_job(job_id) is True
    # Re-registering replaces, never duplicates.
    ac.register_approval_cleanup_job(manager, types.SimpleNamespace())
    assert manager.has_internal_job(job_id) is True


def test_lifespan_registers_cleanup_job(client) -> None:
    manager = client.app.state.cron_manager
    assert manager.has_internal_job("_internal:approval-cleanup") is True