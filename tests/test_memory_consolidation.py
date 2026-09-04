# -*- coding: utf-8 -*-
"""Memory consolidation feature tests.

Covers:
- cron expression validation and config normalisation
- consolidation watermark (state file) round-trip
- pending-archive detection against the watermark
- expired-archive pruning into ``sessions/archived/``
- ``GET/PUT /api/agents/{id}/memory*`` round-trips, path whitelisting
  and background-trigger endpoint behaviour
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentcore.api import app
from agentcore.runtime import memory_consolidation as mc
from agentcore.runtime import memory_router


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client running inside an isolated (temporary) data directory."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Fake workspace for pure-function tests
# ---------------------------------------------------------------------------


class FakeWorkspace:
    """Minimal workspace stand-in backed by a temp directory."""

    def __init__(self, root: Path) -> None:
        self.workspace_dir = root
        self._config: dict[str, Any] = {}

    def get_memory_dir(self) -> Path:
        path = self.workspace_dir / "memory"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_sessions_archive_dir(self) -> Path:
        path = self.get_memory_dir() / "sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def read_memory_file(self, filename: str) -> str | None:
        path = self.get_memory_dir() / filename
        return path.read_text(encoding="utf-8") if path.exists() else None

    def read_agent_config(self) -> dict[str, Any]:
        return dict(self._config)

    def write_agent_config(self, config: dict[str, Any]) -> None:
        self._config = dict(config)


@pytest.fixture
def fake_workspace(tmp_path) -> FakeWorkspace:
    return FakeWorkspace(tmp_path)


def _make_archive(ws: FakeWorkspace, name: str, mtime: float | None = None) -> Path:
    path = ws.get_sessions_archive_dir() / name
    path.write_text(f"# Session Archive — {name[:-3]}\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# ---------------------------------------------------------------------------
# Cron validation and config normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("0 3 * * *", "0 3 * * *"),
        ("*/15 * * * *", "*/15 * * * *"),
        ("30 22 * * 1", "30 22 * * mon"),  # crontab dow normalised
    ],
)
def test_validate_cron_expression_valid(expr: str, expected: str) -> None:
    assert mc.validate_cron_expression(expr) == expected


@pytest.mark.parametrize(
    "expr",
    ["", "garbage", "61 3 * * *", "0 25 * * *", "1 2 3 4 5 6"],
)
def test_validate_cron_expression_invalid(expr: str) -> None:
    with pytest.raises(ValueError):
        mc.validate_cron_expression(expr)


def test_normalize_memory_config_defaults() -> None:
    config = mc.normalize_memory_config({})
    assert config == {
        "enabled": True,
        "consolidate_cron": "0 3 * * *",
        "retain_days": 30,
        "max_memory_kb": 16,
        "timeout_seconds": 600,
    }


def test_normalize_memory_config_overrides() -> None:
    config = mc.normalize_memory_config(
        {
            "enabled": False,
            "consolidate_cron": "0 */6 * * *",
            "retain_days": 7,
            "max_memory_kb": 64,
            "timeout_seconds": 120,
        }
    )
    assert config["enabled"] is False
    assert config["consolidate_cron"] == "0 */6 * * *"
    assert config["retain_days"] == 7
    assert config["max_memory_kb"] == 64
    assert config["timeout_seconds"] == 120


@pytest.mark.parametrize(
    "raw",
    [
        {"consolidate_cron": "not a cron"},
        {"consolidate_cron": ""},
        {"retain_days": 0},
        {"retain_days": "x"},
        {"max_memory_kb": 99999},
        {"timeout_seconds": 0},
        {"timeout_seconds": mc.MAX_TIMEOUT_SECONDS + 1},
    ],
)
def test_normalize_memory_config_invalid(raw: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        mc.normalize_memory_config(raw)


def test_get_memory_config_falls_back_on_invalid(fake_workspace) -> None:
    fake_workspace._config["memory"] = {"consolidate_cron": "broken"}
    assert mc.get_memory_config(fake_workspace) == mc.DEFAULT_MEMORY_CONFIG


def test_save_memory_config_preserves_last_run(fake_workspace) -> None:
    fake_workspace._config["memory"] = {"last_run": {"status": "success"}}
    mc.save_memory_config(fake_workspace, mc.normalize_memory_config({}))
    section = fake_workspace._config["memory"]
    assert section["last_run"] == {"status": "success"}
    assert section["enabled"] is True


# ---------------------------------------------------------------------------
# Watermark / state file
# ---------------------------------------------------------------------------


def test_consolidation_state_round_trip(fake_workspace) -> None:
    assert mc.read_consolidation_state(fake_workspace) == {}
    state = {"last_consolidated_at": "2026-08-25T03:00:00+00:00", "last_consolidated_files": ["2026-08-24.md"]}
    mc.write_consolidation_state(fake_workspace, state)
    assert mc.read_consolidation_state(fake_workspace) == state


def test_consolidation_state_corrupt_file(fake_workspace) -> None:
    path = fake_workspace.get_memory_dir() / mc.CONSOLIDATION_STATE_FILE_NAME
    path.write_text("{not json", encoding="utf-8")
    assert mc.read_consolidation_state(fake_workspace) == {}


# ---------------------------------------------------------------------------
# Pending detection and archive pruning
# ---------------------------------------------------------------------------


def test_pending_without_watermark_returns_all(fake_workspace) -> None:
    _make_archive(fake_workspace, "2026-08-24.md")
    _make_archive(fake_workspace, "2026-08-25.md")
    pending = mc.pending_archive_files(fake_workspace, {})
    assert [p.name for p in pending] == ["2026-08-24.md", "2026-08-25.md"]


def test_pending_respects_watermark(fake_workspace) -> None:
    old = _make_archive(fake_workspace, "2026-08-24.md", mtime=1000.0)
    new = _make_archive(fake_workspace, "2026-08-25.md", mtime=5000.0)
    from datetime import datetime, timezone

    watermark = datetime.fromtimestamp(3000.0, tz=timezone.utc).isoformat()
    pending = mc.pending_archive_files(
        fake_workspace, {"last_consolidated_at": watermark}
    )
    assert [p.name for p in pending] == ["2026-08-25.md"]
    assert old != new  # both exist on disk


def test_pending_ignores_non_archive_files(fake_workspace) -> None:
    _make_archive(fake_workspace, "2026-08-24.md")
    (fake_workspace.get_sessions_archive_dir() / "notes.md").write_text("x")
    pending = mc.pending_archive_files(fake_workspace, {})
    assert [p.name for p in pending] == ["2026-08-24.md"]


def test_move_expired_archives(fake_workspace) -> None:
    from datetime import datetime, timedelta, timezone

    today = datetime.now(tz=timezone.utc).date()
    recent = f"{today.isoformat()}.md"
    expired = f"{(today - timedelta(days=60)).isoformat()}.md"
    _make_archive(fake_workspace, recent)
    _make_archive(fake_workspace, expired)

    moved = mc.move_expired_archives(fake_workspace, retain_days=30)
    assert moved == [expired]

    base = fake_workspace.get_sessions_archive_dir()
    assert (base / recent).exists()
    assert not (base / expired).exists()
    assert (base / mc.ARCHIVED_DIR_NAME / expired).exists()


def test_list_session_archives_separates_archived(fake_workspace) -> None:
    _make_archive(fake_workspace, "2026-08-24.md")
    archived_dir = fake_workspace.get_sessions_archive_dir() / mc.ARCHIVED_DIR_NAME
    archived_dir.mkdir(exist_ok=True)
    (archived_dir / "2026-01-01.md").write_text("old", encoding="utf-8")

    active = mc.list_session_archives(fake_workspace)
    archived = mc.list_session_archives(fake_workspace, archived=True)
    assert [e["name"] for e in active] == ["2026-08-24.md"]
    assert [e["name"] for e in archived] == ["2026-01-01.md"]


def test_build_prompt_lists_files() -> None:
    prompt = mc._build_prompt(["2026-08-24.md", "2026-08-25.md"], 16)
    assert "/memory/sessions/2026-08-24.md" in prompt
    assert "/memory/sessions/2026-08-25.md" in prompt
    assert "16KB" in prompt
    assert "凭据" in prompt


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


def test_memory_get_payload_defaults(client) -> None:
    res = client.get("/api/agents/default/memory")
    assert res.status_code == 200
    payload = res.json()
    assert payload["agent_id"] == "default"
    assert payload["config"]["enabled"] is True
    assert payload["files"]["MEMORY.md"] is not None
    assert payload["files"]["USER.md"] is not None
    assert payload["archives"] == []
    # Default config is enabled → startup hook registered the internal job.
    assert payload["consolidation"]["scheduled"] is True


def test_memory_get_unknown_agent_404(client) -> None:
    assert client.get("/api/agents/nope/memory").status_code == 404


def test_memory_config_round_trip_and_scheduling(client) -> None:
    # Disable → internal job removed.
    res = client.put(
        "/api/agents/default/memory/config",
        json={"enabled": False, "consolidate_cron": "0 4 * * *"},
    )
    assert res.status_code == 200
    assert res.json()["consolidation"]["scheduled"] is False
    assert res.json()["config"]["enabled"] is False

    # Re-enable with a normalisable cron → scheduled again.
    res = client.put(
        "/api/agents/default/memory/config",
        json={"enabled": True, "consolidate_cron": "30 22 * * 1"},
    )
    assert res.status_code == 200
    assert res.json()["consolidation"]["scheduled"] is True
    assert res.json()["config"]["consolidate_cron"] == "30 22 * * mon"


def test_memory_config_invalid_cron_400(client) -> None:
    res = client.put(
        "/api/agents/default/memory/config",
        json={"consolidate_cron": "not valid"},
    )
    assert res.status_code == 400


def test_memory_file_edit_whitelist(client) -> None:
    # Allowed file.
    res = client.put(
        "/api/agents/default/memory/files/MEMORY.md",
        json={"content": "# New memory\n- edited via API\n"},
    )
    assert res.status_code == 200
    assert res.json()["saved"] is True

    payload = client.get("/api/agents/default/memory").json()
    assert "edited via API" in payload["files"]["MEMORY.md"]["content"]

    # Disallowed names are rejected without touching disk.
    for bad_name in ("sessions/2026-01-01.md", "..%2Fagent.json", "agent.json"):
        res = client.put(
            f"/api/agents/default/memory/files/{bad_name}",
            json={"content": "hack"},
        )
        assert res.status_code in (400, 404)

    # Missing content field.
    res = client.put("/api/agents/default/memory/files/USER.md", json={})
    assert res.status_code == 400


def test_memory_archive_read(client) -> None:
    # Seed an archive file directly into the default agent's workspace.
    from agentcore.runtime import paths

    archive_dir = (
        paths.get_agent_workspace_dir("default") / "memory" / "sessions"
    )
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "2026-08-20.md").write_text(
        "# Session Archive — 2026-08-20\n\n"
        "**[user]** _t_\n\nhello memory\n\n---\n\n",
        encoding="utf-8",
    )

    payload = client.get("/api/agents/default/memory").json()
    assert [e["name"] for e in payload["archives"]] == ["2026-08-20.md"]

    res = client.get("/api/agents/default/memory/archives/2026-08-20.md")
    assert res.status_code == 200
    assert "hello memory" in res.json()["content"]

    # Bad names and missing files.  Traversal attempts either fail the
    # name regex (400) or never match the single-segment route (404).
    assert (
        client.get("/api/agents/default/memory/archives/../../agent.json").status_code
        in (400, 404)
    )
    assert (
        client.get("/api/agents/default/memory/archives/not_a_date.md").status_code
        == 400
    )
    assert (
        client.get("/api/agents/default/memory/archives/1999-01-01.md").status_code
        == 404
    )


def test_memory_consolidate_endpoint_background(client, monkeypatch) -> None:
    """POST /consolidate fires a forced background run (stubbed)."""
    records: list[dict[str, Any]] = []

    async def fake_run(state, agent_id, *, force=False, timeout=None):
        records.append({"agent_id": agent_id, "force": force})
        return {"status": "success", "summary": "stub"}

    monkeypatch.setattr(memory_router, "run_consolidation_once", fake_run)

    res = client.post("/api/agents/default/memory/consolidate")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "started"
    assert body["session_id"] == "memory-consolidation-default"
    assert records and records[0]["force"] is True

    # Unknown agent → 404 before any run is scheduled.
    assert client.post("/api/agents/nope/memory/consolidate").status_code == 404
