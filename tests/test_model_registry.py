"""Tests for the model registry (console Models page).

Covers the registry CRUD endpoints, the single-default invariant,
registry-driven agent seeding (new agents follow the default model) and
the extended model factory options (headers / extra_body).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcore.api import app
from agentcore.runtime import model_factory
from agentcore.runtime import model_store


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client running inside an isolated (temporary) data directory."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    """Redirect the registry file into the test's temp directory."""
    registry_path = tmp_path / "models.json"
    monkeypatch.setattr(model_store, "_registry_path", lambda: registry_path)


def _payload(**overrides):
    base = {
        "name": "qwen3.6-plus",
        "provider": "openai",
        "base_url": "https://coding.dashscope.aliyuncs.com/v1",
        "api_key": "sk-test-123",
        "api_key_env": "",
        "headers": [{"key": "x-app", "value": "cli"}],
        "extra_body": {"enable_thinking": False, "max_tokens": 2048},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Registry CRUD
# ---------------------------------------------------------------------------


def test_registry_starts_empty(client) -> None:
    body = client.get("/api/models/library").json()
    assert body == {"models": [], "count": 0}


def test_registry_create_validation(client) -> None:
    assert client.post("/api/models", json={}).status_code == 422
    resp = client.post(
        "/api/models", json={"name": "x", "base_url": "not-a-url"}
    )
    assert resp.status_code == 400
    assert "http" in resp.json()["detail"]
    resp = client.post(
        "/api/models", json={"name": "", "base_url": "https://example.com/v1"}
    )
    assert resp.status_code == 400
    assert "name" in resp.json()["detail"]


def test_registry_create_and_mask_key(client) -> None:
    resp = client.post("/api/models", json=_payload())
    assert resp.status_code == 200
    created = resp.json()
    assert created["name"] == "qwen3.6-plus"
    assert created["api_key_set"] is True
    assert "api_key" not in created  # plaintext key never leaves the server
    assert created["headers"] == [{"key": "x-app", "value": "cli"}]
    assert created["extra_body"] == {"enable_thinking": False, "max_tokens": 2048}
    model_id = created["id"]

    listed = client.get("/api/models/library").json()
    assert listed["count"] == 1
    assert listed["models"][0]["id"] == model_id
    assert listed["models"][0]["api_key_set"] is True


def test_registry_env_ref_counts_as_key_set(client, monkeypatch) -> None:
    """A model referencing an env var is reported as key-configured."""
    monkeypatch.setenv("MY_DEMO_LLM_KEY", "sk-demo-env")
    resp = client.post(
        "/api/models",
        json=_payload(api_key="", api_key_env="MY_DEMO_LLM_KEY"),
    )
    assert resp.status_code == 200
    assert resp.json()["api_key_set"] is True
    assert resp.json()["api_key_env"] == "MY_DEMO_LLM_KEY"


def test_registry_update_and_delete(client) -> None:
    model_id = client.post("/api/models", json=_payload()).json()["id"]

    resp = client.put(
        f"/api/models/{model_id}",
        json=_payload(name="qwen3.6-max", api_key="sk-new"),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "qwen3.6-max"
    assert resp.json()["api_key_set"] is True

    # Unknown id → 404 on update/delete/default.
    assert client.put("/api/models/nope", json=_payload()).status_code == 404
    assert client.delete("/api/models/nope").status_code == 404
    assert client.put("/api/models/nope/default", json={}).status_code == 404

    assert client.delete(f"/api/models/{model_id}").status_code == 200
    assert client.get("/api/models/library").json() == {"models": [], "count": 0}


# ---------------------------------------------------------------------------
# Default model (single, mutually exclusive)
# ---------------------------------------------------------------------------


def test_default_model_is_mutually_exclusive(client) -> None:
    first = client.post("/api/models", json=_payload(is_default=True)).json()
    second = client.post(
        "/api/models", json=_payload(name="other-model", is_default=True)
    ).json()

    library = client.get("/api/models/library").json()["models"]
    defaults = [m for m in library if m["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == second["id"]

    # Switching the default via the dedicated endpoint clears the other.
    resp = client.put(f"/api/models/{first['id']}/default", json={"is_default": True})
    assert resp.status_code == 200
    assert resp.json()["is_default"] is True
    library = client.get("/api/models/library").json()["models"]
    by_id = {m["id"]: m for m in library}
    assert by_id[first["id"]]["is_default"] is True
    assert by_id[second["id"]]["is_default"] is False

    # Deleting the default model leaves no default behind.
    client.delete(f"/api/models/{first['id']}")
    library = client.get("/api/models/library").json()["models"]
    assert all(not m["is_default"] for m in library)


# ---------------------------------------------------------------------------
# Registry-driven agent seeding
# ---------------------------------------------------------------------------


def test_new_agent_seeded_with_default_model(client, tmp_path) -> None:
    default_cfg = _payload(
        name="registry-default",
        base_url="https://gateway.example.com/v1",
        api_key_env="MY_LLM_KEY",
        headers=[{"key": "x-app", "value": "console"}],
        extra_body={"enable_thinking": True},
        is_default=True,
    )
    client.post("/api/models", json=default_cfg)

    resp = client.post("/api/agents", json={"agent_id": "seed-me"})
    assert resp.status_code == 200
    agent_json = Path(".agentcore/workspace/agent/seed-me/agent.json")
    config = json.loads(agent_json.read_text(encoding="utf-8"))
    assert config["model"]["name"] == "registry-default"
    assert config["model"]["base_url"] == "https://gateway.example.com/v1"
    assert config["model"]["api_key_env"] == "MY_LLM_KEY"
    assert config["model"]["headers"] == [{"key": "x-app", "value": "console"}]
    assert config["model"]["extra_body"] == {"enable_thinking": True}


def test_new_agent_keeps_builtin_default_without_registry(client) -> None:
    client.post("/api/agents", json={"agent_id": "no-registry"})
    agent_json = Path(".agentcore/workspace/agent/no-registry/agent.json")
    config = json.loads(agent_json.read_text(encoding="utf-8"))
    assert config["model"]["name"] == "qwen3.6-plus"


def test_put_agent_model_extended_fields(client) -> None:
    client.post("/api/agents", json={"agent_id": "model-ext"})
    resp = client.put(
        "/api/agents/model-ext/model",
        json={
            "provider": "openai",
            "name": "ext-model",
            "base_url": "https://gateway.example.com/v1",
            "api_key": "sk-plain-9",
            "headers": [{"key": "x-app", "value": "cli"}],
            "extra_body": {"enable_thinking": False},
        },
    )
    assert resp.status_code == 200
    agent_json = Path(".agentcore/workspace/agent/model-ext/agent.json")
    config = json.loads(agent_json.read_text(encoding="utf-8"))
    assert config["model"]["api_key"] == "sk-plain-9"
    assert config["model"]["headers"] == [{"key": "x-app", "value": "cli"}]
    assert config["model"]["extra_body"] == {"enable_thinking": False}


# ---------------------------------------------------------------------------
# Model factory: headers / extra_body support
# ---------------------------------------------------------------------------


def test_build_chat_model_headers_and_extra_body(monkeypatch) -> None:
    captured: dict = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)
    model_factory.clear_model_cache()

    model = model_factory.build_chat_model(
        {
            "provider": "openai",
            "name": "test-model",
            "base_url": "https://example.com/v1",
            "api_key": "sk-test",
            "headers": [{"key": "x-app", "value": "cli"}],
            "extra_body": {"enable_thinking": False, "max_tokens": 2048},
        }
    )
    assert model is not None
    assert captured["default_headers"] == {"x-app": "cli"}
    assert captured["extra_body"] == {"enable_thinking": False, "max_tokens": 2048}

    # A dict-shaped headers section is also accepted.
    model_factory.clear_model_cache()
    captured.clear()
    model_factory.build_chat_model(
        {
            "provider": "openai",
            "name": "test-model",
            "base_url": "https://example.com/v1",
            "api_key": "sk-test",
            "headers": {"x-app": "cli"},
        }
    )
    assert captured["default_headers"] == {"x-app": "cli"}


def test_build_chat_model_ignores_headers_without_key(monkeypatch) -> None:
    captured: dict = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)
    model_factory.clear_model_cache()

    model_factory.build_chat_model(
        {
            "provider": "openai",
            "name": "test-model",
            "base_url": "https://example.com/v1",
            "api_key": "sk-test",
            "headers": [{"key": "  ", "value": "x"}, {"value": "no-key"}],
        }
    )
    assert "default_headers" not in captured
