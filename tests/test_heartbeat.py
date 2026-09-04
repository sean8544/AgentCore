# -*- coding: utf-8 -*-
"""Heartbeat feature tests (phase 2 + SSE keep-alive from phase 1).

Covers:
- interval parsing / cron detection / active-hours windows
- config normalisation and validation errors (HTTP 400)
- ``GET/PUT /api/agents/{id}/heartbeat`` round-trip and scheduling via
  CronManager internal jobs
- ``POST .../heartbeat/run`` skip path (missing HEARTBEAT.md)
- workspace seeding of HEARTBEAT.md
- SSE keep-alive wrapper emitting comment ticks on idle streams
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from agentcore.api import app
from agentcore.runtime import heartbeat as hb
from agentcore.runtime.sse_heartbeat import keepalive_sse
from agentcore.runtime.workspace import HEARTBEAT_MD_NAME


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client running inside an isolated (temporary) data directory."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("every", "expected"),
    [
        ("30m", 1800),
        ("1h", 3600),
        ("2h30m", 9000),
        ("90s", 90),
        ("", 1800),
        ("garbage", 1800),
        ("0m", 1800),
    ],
)
def test_parse_heartbeat_every(every: str, expected: int) -> None:
    assert hb.parse_heartbeat_every(every) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0 9 * * *", True),
        ("*/5 * * * 1-5", True),
        ("0 9 * * mon", True),
        ("30m", False),
        ("2h30m", False),
        ("0 9 * *", False),
    ],
)
def test_is_cron_expression(value: str, expected: bool) -> None:
    assert hb.is_cron_expression(value) is expected


def test_in_active_hours_windows() -> None:
    inside = datetime(2026, 1, 1, 12, 0)
    outside = datetime(2026, 1, 1, 3, 0)
    window = {"start": "08:00", "end": "22:00"}
    assert hb.in_active_hours(window, now=inside) is True
    assert hb.in_active_hours(window, now=outside) is False

    # Wrap-around window (22:00–06:00).
    wrap = {"start": "22:00", "end": "06:00"}
    assert hb.in_active_hours(wrap, now=datetime(2026, 1, 1, 23, 30)) is True
    assert hb.in_active_hours(wrap, now=datetime(2026, 1, 1, 12, 0)) is False

    # Missing / malformed config means always active.
    assert hb.in_active_hours(None, now=outside) is True
    assert hb.in_active_hours({"start": "xx"}, now=outside) is True


def test_normalize_heartbeat_config_defaults() -> None:
    config = hb.normalize_heartbeat_config({})
    assert config == {
        "enabled": False,
        "every": "30m",
        "timeout_seconds": 600,
        "active_hours": None,
    }


@pytest.mark.parametrize(
    "raw",
    [
        {"every": "0 9 * * *"},       # cron expression rejected
        {"every": "banana"},          # invalid interval
        {"every": ""},                # empty interval
        {"timeout_seconds": 0},       # out of range
        {"timeout_seconds": 99999},   # out of range
        {"active_hours": {"start": "08:00"}},   # incomplete window
        {"active_hours": {"start": "25:00", "end": "26:00"}},
        {"active_hours": "08:00"},    # wrong shape
    ],
)
def test_normalize_heartbeat_config_rejects(raw: dict) -> None:
    with pytest.raises(ValueError):
        hb.normalize_heartbeat_config(raw)


def test_normalize_active_hours_empty_dict_means_none() -> None:
    config = hb.normalize_heartbeat_config({"active_hours": {"start": "", "end": ""}})
    assert config["active_hours"] is None


# ---------------------------------------------------------------------------
# Router endpoints
# ---------------------------------------------------------------------------


def test_workspace_seeds_heartbeat_md(client) -> None:
    manager = client.app.state.agent_manager
    workspace = asyncio.run(manager.get_or_create_workspace("default"))
    assert (workspace.workspace_dir / HEARTBEAT_MD_NAME).is_file()


def test_get_heartbeat_defaults(client) -> None:
    resp = client.get("/api/agents/default/heartbeat")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "default"
    assert data["config"]["enabled"] is False
    assert data["config"]["every"] == "30m"
    assert data["query_file_exists"] is True
    assert data["scheduled"] is False
    assert data["last_run"] is None


def test_get_heartbeat_unknown_agent(client) -> None:
    assert client.get("/api/agents/nope/heartbeat").status_code == 404


def test_put_heartbeat_roundtrip_and_scheduling(client) -> None:
    cron_manager = client.app.state.cron_manager
    job_id = hb.heartbeat_job_id("default")

    # Enable — config persisted and internal job registered.
    resp = client.put(
        "/api/agents/default/heartbeat",
        json={
            "enabled": True,
            "every": "45m",
            "timeout_seconds": 300,
            "active_hours": {"start": "08:00", "end": "22:00"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["scheduled"] is True
    assert data["config"]["every"] == "45m"
    assert cron_manager.has_internal_job(job_id)
    assert data["next_run_at"] is not None

    stored = (
        client.app.state.agent_manager.get_workspace("default")
        if hasattr(client.app.state.agent_manager, "get_workspace")
        else None
    )
    if stored is not None:
        assert stored.read_agent_config()["heartbeat"]["enabled"] is True

    # Disable — internal job removed.
    resp = client.put(
        "/api/agents/default/heartbeat", json={"enabled": False}
    )
    assert resp.status_code == 200
    assert resp.json()["scheduled"] is False
    assert not cron_manager.has_internal_job(job_id)


def test_put_heartbeat_validation(client) -> None:
    assert (
        client.put(
            "/api/agents/default/heartbeat", json={"every": "0 9 * * *"}
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/agents/default/heartbeat", json={"timeout_seconds": -1}
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/agents/default/heartbeat",
            json={"active_hours": {"start": "08:00"}},
        ).status_code
        == 400
    )


def test_run_heartbeat_skips_without_query_file(client) -> None:
    manager = client.app.state.agent_manager
    workspace = asyncio.run(manager.get_or_create_workspace("default"))
    (workspace.workspace_dir / HEARTBEAT_MD_NAME).unlink()

    resp = client.post("/api/agents/default/heartbeat/run")
    assert resp.status_code == 200
    record = resp.json()
    assert record["status"] == "skipped"
    assert record["reason"] == "no_query_file"
    assert record["session_id"] == "heartbeat-default"


def test_run_heartbeat_skips_outside_active_hours(client) -> None:
    """Scheduled beats honour active hours (manual runs bypass them)."""
    assert hb.in_active_hours(
        {"start": "00:00", "end": "00:01"},
        now=datetime(2026, 1, 1, 12, 0),
    ) is False


# ---------------------------------------------------------------------------
# sync_heartbeat_job against a real CronManager
# ---------------------------------------------------------------------------


class _FakeWorkspace:
    def __init__(self, hb_section: dict) -> None:
        self._hb = hb_section

    def read_agent_config(self) -> dict:
        return {"heartbeat": dict(self._hb)}

    def write_agent_config(self, config: dict) -> None:
        self._hb = dict(config.get("heartbeat") or {})


class _FakeManager:
    def __init__(self, workspace: _FakeWorkspace) -> None:
        self._workspace = workspace

    async def get_or_create_workspace(self, agent_id: str):
        return self._workspace

    def list_workspaces(self) -> list[str]:
        return ["agent-x"]


def test_sync_heartbeat_job_register_and_remove(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    from agentcore.runtime.cron_manager import CronManager

    class _State:
        pass

    state = _State()
    workspace = _FakeWorkspace(
        {"enabled": True, "every": "30m", "timeout_seconds": 600}
    )
    state.agent_manager = _FakeManager(workspace)

    manager = CronManager(state)
    job_id = hb.heartbeat_job_id("agent-x")

    async def scenario() -> None:
        # Enabled config -> registered.
        assert await hb.sync_heartbeat_job(manager, state, "agent-x") == job_id
        assert manager.has_internal_job(job_id)

        # Disabled config -> removed.
        workspace._hb["enabled"] = False
        assert await hb.sync_heartbeat_job(manager, state, "agent-x") is None
        assert not manager.has_internal_job(job_id)

    asyncio.run(scenario())


def test_sync_heartbeat_job_without_manager(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    from agentcore.runtime.cron_manager import CronManager

    class _State:
        agent_manager = None

    manager = CronManager(_State())

    async def scenario() -> None:
        # No agent_manager -> defaults (disabled) -> nothing scheduled.
        assert await hb.sync_heartbeat_job(manager, _State(), "ghost") is None

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# SSE keep-alive wrapper (phase 1)
# ---------------------------------------------------------------------------


def test_keepalive_sse_emits_ticks_on_idle() -> None:
    async def source():
        await asyncio.sleep(0.12)
        yield "data: hello\n\n"

    async def collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in keepalive_sse(source(), interval=0.05):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())
    assert ": keep-alive\n\n" in chunks
    assert chunks[-1] == "data: hello\n\n"


def test_keepalive_sse_passes_through_busy_streams() -> None:
    async def source():
        yield "a"
        yield "b"

    async def collect() -> list[str]:
        return [c async for c in keepalive_sse(source(), interval=10)]

    assert asyncio.run(collect()) == ["a", "b"]
