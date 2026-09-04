"""Sandbox control-plane endpoint tests.

These cover the *global* control-plane added under ``/api/sandbox``:

- ``GET/PUT /api/sandbox/settings`` — defaults, persistence, masking, validation
- ``POST /api/sandbox/settings/test`` — reachability probe shape
- ``POST /api/sandbox/start`` / ``/stop`` — in-process session-manager toggle
- ``GET /api/sandbox/overview`` — degraded structure when unreachable, and the
  cross-container CPU/memory aggregation when reachable
- ``GET /api/sandbox/containers`` / ``/images`` — thin pass-through + client-side
  image aggregation
- ``GET /api/sandbox/server-guide`` — purely informational quick-start data

All OpenSandbox SDK access is monkey-patched out (no network / no server).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcore.api import app
from agentcore.sandbox import client as client_mod
from agentcore.sandbox.client import SandboxInfo


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client running inside an isolated (temporary) data directory."""
    monkeypatch.chdir(tmp_path)
    # Make sure env does not pre-enable the sandbox for these tests.
    monkeypatch.delenv("SANDBOX_ENABLED", raising=False)
    with TestClient(app) as test_client:
        yield test_client


def _settings_file() -> Path:
    return Path(".agentcore/sandbox.json")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeClient:
    """Stands in for :class:`OpenSandboxClient` (no SDK / network)."""

    # Class-level knobs the tests tweak before issuing a request.
    reachable: bool = True
    version: str = "0.1.16"
    infos: list[SandboxInfo] = []
    metrics_map: dict[str, dict] = {}
    raise_on_list: bool = False

    def __init__(self, config) -> None:
        self.config = config

    async def health_check(self) -> bool:
        return type(self).reachable

    async def get_server_version(self) -> str:
        return type(self).version

    async def list_sandboxes(self) -> list[SandboxInfo]:
        if type(self).raise_on_list:
            raise RuntimeError("boom")
        return list(type(self).infos)

    async def get_metrics(self, sandbox_id: str) -> dict:
        return type(self).metrics_map.get(sandbox_id, {})

    async def pause_sandbox(self, sandbox_id: str) -> None:
        return None

    async def resume_sandbox(self, sandbox_id: str) -> None:
        return None

    async def renew_sandbox(self, sandbox_id: str, timeout: int) -> None:
        return None

    async def destroy_sandbox(self, sandbox_id: str) -> None:
        return None

    async def get_diagnostic_logs(self, sandbox_id: str) -> list[str]:
        return [f"log-line-1 for {sandbox_id}"]


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    """Route every control-plane call through ``_FakeClient``."""
    _FakeClient.reachable = True
    _FakeClient.version = "0.1.16"
    _FakeClient.infos = []
    _FakeClient.metrics_map = {}
    _FakeClient.raise_on_list = False
    monkeypatch.setattr(client_mod, "OpenSandboxClient", _FakeClient)
    yield
    _FakeClient.infos = []
    _FakeClient.metrics_map = {}


class _FakeManager:
    def __init__(self, config) -> None:
        self.config = config
        self.destroyed = False
        self.idle_checker_running = False

    async def health_check(self) -> dict:
        return {
            "server_reachable": True,
            "server_url": self.config.base_url,
            "active_sessions": 0,
            "enabled": True,
        }

    def start_idle_checker(self) -> None:
        self.idle_checker_running = True

    async def stop_idle_checker(self) -> None:
        self.idle_checker_running = False

    async def destroy_all(self) -> None:
        self.destroyed = True


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_get_settings_defaults(client) -> None:
    resp = client.get("/api/sandbox/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["domain"] == "localhost:8080"
    assert data["protocol"] == "http"
    assert data["base_url"] == "http://localhost:8080"
    assert data["api_key_set"] is False
    # Never leaks the raw key.
    assert "api_key" not in data


def test_put_settings_roundtrip_and_masking(client) -> None:
    resp = client.put(
        "/api/sandbox/settings",
        json={
            "domain": "sandbox.internal:9000",
            "protocol": "https",
            "api_key": "secret-123",
            "default_cleanup": "never",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["domain"] == "sandbox.internal:9000"
    assert data["protocol"] == "https"
    assert data["base_url"] == "https://sandbox.internal:9000"
    assert data["default_cleanup"] == "never"
    assert data["api_key_set"] is True
    assert "api_key" not in data

    # Persisted to disk in plaintext (envs/models parity).
    stored = json.loads(_settings_file().read_text(encoding="utf-8"))
    assert stored["api_key"] == "secret-123"

    # Survives a fresh read (masked again).
    assert client.get("/api/sandbox/settings").json() == data


def test_put_settings_keeps_existing_key_when_omitted(client) -> None:
    client.put("/api/sandbox/settings", json={"api_key": "keep-me"})
    resp = client.put("/api/sandbox/settings", json={"domain": "h:1234"})
    assert resp.status_code == 200
    stored = json.loads(_settings_file().read_text(encoding="utf-8"))
    assert stored["api_key"] == "keep-me"
    assert stored["domain"] == "h:1234"


def test_put_settings_validation(client) -> None:
    assert (
        client.put("/api/sandbox/settings", json={"protocol": "ftp"}).status_code
        == 400
    )
    assert (
        client.put(
            "/api/sandbox/settings", json={"default_cleanup": "sometimes"}
        ).status_code
        == 400
    )


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------


def test_settings_test_reachable(client) -> None:
    _FakeClient.reachable = True
    resp = client.post("/api/sandbox/settings/test", json={"domain": "h:1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reachable"] is True
    assert data["base_url"] == "http://h:1"
    assert data["version"] == "0.1.16"
    assert isinstance(data["latency_ms"], int)


def test_settings_test_unreachable(client) -> None:
    _FakeClient.reachable = False
    resp = client.post("/api/sandbox/settings/test", json={})
    data = resp.json()
    assert data["reachable"] is False
    assert data["version"] is None
    assert data["error"] == "server unreachable"


# ---------------------------------------------------------------------------
# Start / stop toggle
# ---------------------------------------------------------------------------


def test_start_then_stop_toggles_state(client, monkeypatch) -> None:
    import agentcore.sandbox.lifecycle as lifecycle_mod
    import agentcore.sandbox.router as router_mod

    monkeypatch.setattr(lifecycle_mod, "SandboxSessionManager", _FakeManager)
    # The router imports the symbol at call time from the lifecycle module.
    monkeypatch.setattr(router_mod, "SandboxSessionManager", _FakeManager, raising=False)

    start = client.post("/api/sandbox/start")
    assert start.status_code == 200
    assert start.json()["status"] == "started"
    manager = client.app.state.sandbox_session_manager
    assert manager is not None
    # Enabling must also launch the idle loop — pause-on-idle / ephemeral
    # strategies silently no-op without it.
    assert manager.idle_checker_running is True
    assert client.get("/api/sandbox/settings").json()["enabled"] is True
    assert json.loads(_settings_file().read_text(encoding="utf-8"))["enabled"] is True

    stop = client.post("/api/sandbox/stop")
    assert stop.status_code == 200
    assert stop.json()["status"] == "stopped"
    assert manager.idle_checker_running is False
    assert client.app.state.sandbox_session_manager is None
    assert client.get("/api/sandbox/settings").json()["enabled"] is False


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def test_overview_degraded_when_unreachable(client) -> None:
    _FakeClient.reachable = False
    resp = client.get("/api/sandbox/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["server_reachable"] is False
    assert data["containers"]["total"] == 0
    assert data["cpu"]["total_cores"] is None
    assert data["memory"]["total_mib"] is None


def test_overview_aggregates_when_reachable(client) -> None:
    _FakeClient.reachable = True
    now = datetime.now(timezone.utc)
    _FakeClient.infos = [
        SandboxInfo(
            sandbox_id="sbx-run",
            agent_id="agent-a",
            image="opensandbox/execd:v1",
            status="running",
            created_at=now - timedelta(seconds=30),
            expires_at=now + timedelta(seconds=600),
        ),
        SandboxInfo(
            sandbox_id="sbx-paused",
            agent_id="agent-b",
            image="opensandbox/execd:v1",
            status="paused",
        ),
    ]
    _FakeClient.metrics_map = {
        "sbx-run": {
            "cpu_count": 2,
            "cpu_used_percentage": 40.0,
            "memory_total_in_mib": 1024.0,
            "memory_used_in_mib": 256.0,
        }
    }
    resp = client.get("/api/sandbox/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["server_reachable"] is True
    assert data["version"] == "0.1.16"
    assert data["containers"] == {"total": 2, "running": 1, "paused": 1, "other": 0}
    assert data["cpu"]["total_cores"] == 2
    assert data["cpu"]["used_percentage"] == 40.0
    assert data["memory"]["total_mib"] == 1024.0
    assert data["memory"]["used_mib"] == 256.0


# ---------------------------------------------------------------------------
# Containers / images
# ---------------------------------------------------------------------------


def test_containers_passthrough(client) -> None:
    now = datetime.now(timezone.utc)
    _FakeClient.infos = [
        SandboxInfo(
            sandbox_id="sbx-1",
            agent_id="agent-x",
            image="img:v9",
            status="running",
            platform="linux/amd64",
            created_at=now,
            expires_at=None,
        )
    ]
    resp = client.get("/api/sandbox/containers")
    assert resp.status_code == 200
    data = resp.json()
    assert data["reachable"] is True
    assert data["containers"][0]["sandbox_id"] == "sbx-1"
    assert data["containers"][0]["agent_id"] == "agent-x"
    assert data["containers"][0]["image"] == "img:v9"
    assert data["containers"][0]["platform"] == "linux/amd64"


def test_containers_unreachable_degrades(client) -> None:
    _FakeClient.raise_on_list = True
    resp = client.get("/api/sandbox/containers")
    data = resp.json()
    assert data["containers"] == []
    assert data["reachable"] is False


def test_images_aggregation(client) -> None:
    _FakeClient.infos = [
        SandboxInfo(sandbox_id="a", image="img:v1"),
        SandboxInfo(sandbox_id="b", image="img:v1"),
        SandboxInfo(sandbox_id="c", image="img:v2"),
    ]
    resp = client.get("/api/sandbox/images")
    assert resp.status_code == 200
    images = {i["image"]: i["count"] for i in resp.json()["images"]}
    assert images == {"img:v1": 2, "img:v2": 1}


# ---------------------------------------------------------------------------
# Single-container ops (pass-through returns)
# ---------------------------------------------------------------------------


def test_container_ops_return_status(client) -> None:
    assert client.post("/api/sandbox/containers/sbx-1/pause").json()["status"] == "paused"
    assert client.post("/api/sandbox/containers/sbx-1/resume").json()["status"] == "resumed"
    assert client.post("/api/sandbox/containers/sbx-1/renew").json()["status"] == "renewed"
    assert client.post("/api/sandbox/containers/sbx-1/destroy").json()["status"] == "destroyed"


def test_container_metrics_passthrough(client) -> None:
    _FakeClient.metrics_map = {"sbx-1": {"cpu_count": 4, "cpu_used_percentage": 12.5}}
    resp = client.get("/api/sandbox/containers/sbx-1/metrics")
    assert resp.status_code == 200
    assert resp.json()["cpu_count"] == 4


# ---------------------------------------------------------------------------
# Server guide
# ---------------------------------------------------------------------------


def test_server_guide_shape(client) -> None:
    resp = client.get("/api/sandbox/server-guide")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["prerequisites"]) >= {"docker", "uv"}
    assert isinstance(data["quickstart_commands"]["windows"], list)
    assert isinstance(data["quickstart_commands"]["linux"], list)
    assert data["default_url"] == "http://localhost:8080"
    assert data["docs_url"]
    assert data["script_paths"]["windows"].endswith("opensandbox-quickstart.ps1")
    assert data["script_paths"]["linux"].endswith("opensandbox-quickstart.sh")
    # AIO Sandbox requires seccomp=unconfined — must be advertised so the UI can warn.
    aio = data["aio_requirements"]
    assert aio["seccomp_profile"] == "unconfined"
    assert aio["config_key"] == "[docker].seccomp_profile"
    assert aio["config_file"].endswith(".sandbox.toml")
    assert "seccomp=unconfined" in aio["docker_equivalent"]
