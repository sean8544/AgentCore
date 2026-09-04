"""Tests for the cron job feature (cron_manager + cron_router).

Covers:

- schedule spec validation / crontab day-of-week normalisation
- JSON store roundtrip + history cap
- trigger building and invalid-job auto-disable
- full API CRUD against the real app lifespan (isolated data dir)
- manual execution closed loop with a fake agent graph (success +
  HITL interrupt paths)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from agentcore.api import app
from agentcore.runtime import chat_router
from agentcore.runtime.cron_manager import (
    CronJobSpec,
    CronManager,
    CronStore,
    ScheduleSpec,
    normalize_cron_5_fields,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client with an isolated data directory (lifespan fully runs)."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def store(tmp_path) -> CronStore:
    return CronStore(path=tmp_path / "cron_jobs.json")


def _spec(**overrides) -> CronJobSpec:
    base = {
        "id": "job-1",
        "name": "daily report",
        "agent_id": "default",
        "message": "summarise today",
        "schedule": {"type": "cron", "cron": "0 9 * * 1-5"},
    }
    base.update(overrides)
    return CronJobSpec.model_validate(base)


def _wait_for_status(
    client: TestClient, job_id: str, statuses, timeout: float = 10.0
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = client.get(f"/api/cron/jobs/{job_id}/state").json()
        if last.get("last_status") in statuses:
            return last
        time.sleep(0.1)
    raise AssertionError(
        f"job {job_id} never reached {statuses}; last state: {last}"
    )


# ---------------------------------------------------------------------------
# Schedule spec / cron normalisation
# ---------------------------------------------------------------------------


class TestScheduleSpec:
    def test_dow_numbers_convert_to_names(self):
        # crontab 0=sunday; APScheduler expects ISO numbering — the
        # validator normalises to unambiguous abbreviations.
        assert normalize_cron_5_fields("0 9 * * 0") == "0 9 * * sun"
        assert normalize_cron_5_fields("0 9 * * 7") == "0 9 * * sun"
        assert normalize_cron_5_fields("0 9 * * 1-5") == "0 9 * * mon-fri"
        assert normalize_cron_5_fields("*/5 * * * *") == "*/5 * * * *"
        assert normalize_cron_5_fields("0 9 * * mon") == "0 9 * * mon"

    def test_short_forms_are_padded(self):
        assert normalize_cron_5_fields("9 * * 1") == "0 9 * * mon"
        assert normalize_cron_5_fields("* * 0") == "0 0 * * sun"

    def test_six_fields_rejected(self):
        with pytest.raises(ValueError):
            normalize_cron_5_fields("0 0 9 * * *")

    def test_cron_type_requires_expression(self):
        with pytest.raises(ValueError):
            ScheduleSpec(type="cron", cron="")

    def test_once_type_requires_run_at(self):
        with pytest.raises(ValueError):
            ScheduleSpec(type="once")

    def test_cron_type_drops_run_at(self):
        spec = ScheduleSpec(
            type="cron", cron="0 9 * * *", run_at="2030-01-01T00:00:00"
        )
        assert spec.run_at is None


class TestCronJobSpec:
    def test_rejects_empty_message(self):
        with pytest.raises(ValueError):
            _spec(message="   ")

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError):
            _spec(name="")


# ---------------------------------------------------------------------------
# Store persistence
# ---------------------------------------------------------------------------


class TestCronStore:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "jobs.json"
        s1 = CronStore(path=path)
        s1.upsert_job(_spec())
        s1.upsert_job(_spec(id="job-2", name="other"))

        s2 = CronStore(path=path)
        assert {j.id for j in s2.list_jobs()} == {"job-1", "job-2"}

    def test_delete_removes_job_and_history(self, store):
        from agentcore.runtime.cron_manager import CronExecutionRecord
        from datetime import datetime, timezone

        store.upsert_job(_spec())
        store.append_history(
            "job-1",
            CronExecutionRecord(
                run_at=datetime.now(timezone.utc), status="success"
            ),
        )
        assert store.delete_job("job-1") is True
        assert store.get_job("job-1") is None
        assert store.get_history("job-1") == []
        assert store.delete_job("job-1") is False

    def test_history_is_capped(self, store):
        from agentcore.runtime.cron_manager import (
            CRON_HISTORY_LIMIT,
            CronExecutionRecord,
        )
        from datetime import datetime, timezone

        store.upsert_job(_spec())
        for _ in range(CRON_HISTORY_LIMIT + 5):
            store.append_history(
                "job-1",
                CronExecutionRecord(
                    run_at=datetime.now(timezone.utc), status="success"
                ),
            )
        assert len(store.get_history("job-1")) == CRON_HISTORY_LIMIT

    def test_corrupt_job_entry_does_not_break_load(self, tmp_path):
        path = tmp_path / "jobs.json"
        good = _spec().model_dump(mode="json")
        bad = _spec(id="job-bad").model_dump(mode="json")
        # 6 段（秒级）表达式 ⇒ 校验期直接拒绝。
        bad["schedule"]["cron"] = "0 0 9 * * *"
        path.write_text(
            json.dumps({"version": 1, "jobs": [bad, good]}),
            encoding="utf-8",
        )
        s = CronStore(path=path)
        assert [j.id for j in s.list_jobs()] == ["job-1"]


# ---------------------------------------------------------------------------
# Manager: triggers and registration
# ---------------------------------------------------------------------------


class TestCronManager:
    def _manager(self, tmp_path) -> CronManager:
        return CronManager(
            SimpleNamespace(), store=CronStore(path=tmp_path / "j.json")
        )

    def test_build_trigger_types(self, tmp_path):
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.date import DateTrigger

        mgr = self._manager(tmp_path)
        assert isinstance(
            mgr._build_trigger(_spec()), CronTrigger
        )
        once = _spec(
            schedule={"type": "once", "run_at": "2030-01-01T09:00:00+00:00"}
        )
        assert isinstance(mgr._build_trigger(once), DateTrigger)

    def test_invalid_timezone_job_auto_disabled_on_start(self, tmp_path):
        async def _run():
            mgr = self._manager(tmp_path)
            bad = _spec(schedule={
                "type": "cron",
                "cron": "0 9 * * *",
                "timezone": "Not/AZone",
            })
            mgr._store.upsert_job(bad)
            await mgr.start()
            try:
                assert mgr._store.get_job("job-1").enabled is False
                assert mgr._scheduler.get_job("job-1") is None
            finally:
                await mgr.stop()

        asyncio.run(_run())

    def test_paused_job_stays_registered_but_paused(self, tmp_path):
        async def _run():
            mgr = self._manager(tmp_path)
            mgr._store.upsert_job(_spec(enabled=False))
            await mgr.start()
            try:
                aps_job = mgr._scheduler.get_job("job-1")
                assert aps_job is not None
                assert aps_job.next_run_time is None  # paused
            finally:
                await mgr.stop()

        asyncio.run(_run())

    def test_crud_and_history_via_manager(self, tmp_path):
        async def _run():
            mgr = self._manager(tmp_path)
            await mgr.start()
            try:
                await mgr.create_or_replace_job(_spec())
                assert len(mgr.list_views()) == 1
                view = mgr.list_views()[0]
                assert view.spec.id == "job-1"
                assert view.state.next_run_at is not None

                await mgr.pause_job("job-1")
                assert mgr.get_job("job-1").enabled is False
                await mgr.resume_job("job-1")
                assert mgr.get_job("job-1").enabled is True

                with pytest.raises(KeyError):
                    await mgr.pause_job("missing")
                assert await mgr.delete_job("job-1") is True
                assert mgr.list_views() == []
            finally:
                await mgr.stop()

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# API integration (real lifespan, isolated data dir)
# ---------------------------------------------------------------------------


def test_api_crud_lifecycle(client):
    payload = {
        "name": "每日巡检",
        "agent_id": "default",
        "message": "检查系统状态",
        "schedule": {"type": "cron", "cron": "0 9 * * 1-5"},
    }
    res = client.post("/api/cron/jobs", json=payload)
    assert res.status_code == 200
    job = res.json()
    job_id = job["id"]
    assert job_id
    # crontab dow numbers are normalised to names.
    assert job["schedule"]["cron"] == "0 9 * * mon-fri"

    listed = client.get("/api/cron/jobs").json()
    assert len(listed) == 1
    assert listed[0]["spec"]["id"] == job_id
    assert listed[0]["state"]["next_run_at"] is not None

    # Update via PUT.
    payload["name"] = "每日巡检-改"
    res = client.put(f"/api/cron/jobs/{job_id}", json=payload)
    assert res.status_code == 200
    assert res.json()["name"] == "每日巡检-改"

    # Pause / resume.
    assert client.post(f"/api/cron/jobs/{job_id}/pause").json() == {
        "paused": True
    }
    detail = client.get(f"/api/cron/jobs/{job_id}").json()
    assert detail["spec"]["enabled"] is False
    assert client.post(f"/api/cron/jobs/{job_id}/resume").json() == {
        "resumed": True
    }

    # 404 paths.
    assert client.get("/api/cron/jobs/nope").status_code == 404
    assert client.delete("/api/cron/jobs/nope").status_code == 404
    assert client.post("/api/cron/jobs/nope/run").status_code == 404

    # Invalid schedule rejected with 422.
    bad = dict(payload, schedule={"type": "cron", "cron": "0 0 9 * * *"})
    assert client.post("/api/cron/jobs", json=bad).status_code == 422

    assert client.delete(f"/api/cron/jobs/{job_id}").json() == {
        "deleted": True
    }
    assert client.get("/api/cron/jobs").json() == []


def test_api_manual_run_executes_agent(client, monkeypatch):
    """POST /run drives a full fake-agent turn into the session store."""
    calls = []

    class FakeGraph:
        async def ainvoke(self, input_data, config=None):
            calls.append(config)
            return {
                "messages": [
                    HumanMessage(content=input_data["messages"][0].content),
                    AIMessage(content="cron done"),
                ]
            }

    monkeypatch.setattr(
        chat_router, "_resolve_agent_graph", lambda *a, **k: FakeGraph()
    )

    res = client.post(
        "/api/cron/jobs",
        json={
            "name": "manual",
            "agent_id": "default",
            "message": "hello cron",
            "schedule": {"type": "cron", "cron": "0 3 * * *"},
        },
    )
    job_id = res.json()["id"]

    assert client.post(f"/api/cron/jobs/{job_id}/run").json() == {
        "started": True
    }
    state = _wait_for_status(client, job_id, ("success", "error"))
    assert state["last_status"] == "success", state
    assert len(calls) == 1

    # Execution history records the manual trigger.
    history = client.get(f"/api/cron/jobs/{job_id}/history").json()
    assert len(history) == 1
    assert history[0]["trigger"] == "manual"
    assert history[0]["status"] == "success"

    # The dedicated session ``cron:{job_id}`` holds user + assistant msgs.
    store = client.app.state.store
    session = store.load_session(f"cron:{job_id}")
    assert session is not None
    roles = [m["role"] for m in session["messages"]]
    assert roles == ["user", "assistant"]
    assert session["messages"][1]["content"] == "cron done"
    assert session["messages"][1].get("cron_job_id") == job_id


def test_api_manual_run_hitl_interrupt_marks_interrupted(client, monkeypatch):
    fake_interrupt = SimpleNamespace(
        id="i1",
        value={
            "action_requests": [
                {"name": "delete_file", "args": {}, "description": "del"}
            ],
            "review_configs": [],
        },
    )

    class FakeGraph:
        async def ainvoke(self, input_data, config=None):
            return {
                "messages": [AIMessage(content="")],
                "__interrupt__": [fake_interrupt],
            }

    monkeypatch.setattr(
        chat_router, "_resolve_agent_graph", lambda *a, **k: FakeGraph()
    )

    res = client.post(
        "/api/cron/jobs",
        json={
            "name": "risky",
            "agent_id": "default",
            "message": "clean up files",
            "schedule": {"type": "cron", "cron": "0 3 * * *"},
            "runtime": {"share_session": False},
        },
    )
    job_id = res.json()["id"]
    client.post(f"/api/cron/jobs/{job_id}/run")
    state = _wait_for_status(client, job_id, ("interrupted", "error"))
    assert state["last_status"] == "interrupted", state

    history = client.get(f"/api/cron/jobs/{job_id}/history").json()
    assert history[0]["status"] == "interrupted"

    # share_session=False ⇒ per-run session id ``cron:{job}:{run}``.
    store = client.app.state.store
    sessions = [
        sid for sid in store.load_sessions() if sid.startswith(f"cron:{job_id}:")
    ]
    assert len(sessions) == 1
    msgs = store.load_session(sessions[0])["messages"]
    # The approval request is persisted for the user to continue.
    assert msgs[-1].get("approval_request") is not None


def test_once_job_runs_and_auto_disables(client, monkeypatch):
    """A scheduled once-job fires through the real scheduler loop."""

    class FakeGraph:
        async def ainvoke(self, input_data, config=None):
            return {"messages": [AIMessage(content="once done")]}

    monkeypatch.setattr(
        chat_router, "_resolve_agent_graph", lambda *a, **k: FakeGraph()
    )

    res = client.post(
        "/api/cron/jobs",
        json={
            "name": "once",
            "agent_id": "default",
            "message": "one shot",
            "schedule": {
                "type": "once",
                "run_at": "2099-01-01T00:00:00+00:00",
            },
        },
    )
    job_id = res.json()["id"]

    # Run it manually first to verify the once path end-to-end, then
    # reschedule via PUT with a near-future run_at to exercise the
    # real scheduler trigger.
    client.post(f"/api/cron/jobs/{job_id}/run")
    _wait_for_status(client, job_id, ("success", "error"))

    from datetime import datetime, timedelta, timezone

    soon = (
        datetime.now(timezone.utc) + timedelta(milliseconds=500)
    ).isoformat()
    res = client.put(
        f"/api/cron/jobs/{job_id}",
        json={
            "name": "once",
            "agent_id": "default",
            "message": "one shot",
            "schedule": {"type": "once", "run_at": soon},
        },
    )
    assert res.status_code == 200

    # The scheduled run must append a second history record — polling on
    # last_status alone would pass instantly (the manual run already set
    # it to success).
    deadline = time.time() + 15.0
    while time.time() < deadline:
        history = client.get(f"/api/cron/jobs/{job_id}/history").json()
        if len(history) >= 2:
            break
        time.sleep(0.1)
    history = client.get(f"/api/cron/jobs/{job_id}/history").json()
    assert len(history) == 2, history
    assert history[-1]["trigger"] == "scheduled"
    assert history[-1]["status"] == "success"

    # Once exhausted ⇒ auto-disabled and no next run.
    detail = client.get(f"/api/cron/jobs/{job_id}").json()
    assert detail["spec"]["enabled"] is False
    assert detail["state"]["next_run_at"] is None
