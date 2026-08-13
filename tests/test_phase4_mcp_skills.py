"""Phase 4 tests — MCP configuration management & skill pool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentcore.api import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient bound to the app with a scratch data directory."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# MCP configuration CRUD
# ---------------------------------------------------------------------------


def test_mcp_crud_roundtrip(client):
    agent = "default"

    # Empty at first.
    res = client.get(f"/api/agents/{agent}/mcp")
    assert res.status_code == 200
    assert res.json()["servers"] == []

    # Add a stdio server.
    res = client.post(
        f"/api/agents/{agent}/mcp",
        json={
            "server_id": "filesystem",
            "name": "文件系统",
            "transport": "stdio",
            "command": "npx -y @modelcontextprotocol/server-filesystem /tmp",
            "enabled": True,
        },
    )
    assert res.status_code == 200

    # Persisted to the workspace mcp.json.
    mcp_json = Path(".agentcore/workspace/agent/default/mcp.json")
    assert mcp_json.exists()
    stored = json.loads(mcp_json.read_text(encoding="utf-8"))
    assert "filesystem" in stored
    assert stored["filesystem"]["transport"] == "stdio"

    # List returns the entry.
    res = client.get(f"/api/agents/{agent}/mcp")
    servers = res.json()["servers"]
    assert len(servers) == 1
    assert servers[0]["server_id"] == "filesystem"

    # Toggle disables it.
    res = client.patch(f"/api/agents/{agent}/mcp/filesystem/toggle")
    assert res.status_code == 200
    assert res.json()["server"]["enabled"] is False

    # Update url-based entry fields.
    res = client.put(
        f"/api/agents/{agent}/mcp/filesystem",
        json={"transport": "sse", "url": "http://localhost:9000/sse"},
    )
    assert res.status_code == 200
    assert res.json()["server"]["transport"] == "sse"

    # Delete.
    res = client.delete(f"/api/agents/{agent}/mcp/filesystem")
    assert res.status_code == 200
    assert client.get(f"/api/agents/{agent}/mcp").json()["servers"] == []

    # Deleting again → 404.
    res = client.delete(f"/api/agents/{agent}/mcp/filesystem")
    assert res.status_code == 404


def test_mcp_add_validation(client):
    # Missing server_id.
    res = client.post("/api/agents/default/mcp", json={"transport": "stdio"})
    assert res.status_code == 400

    # Invalid server_id characters.
    res = client.post(
        "/api/agents/default/mcp",
        json={"server_id": "../evil", "transport": "stdio", "command": "x"},
    )
    assert res.status_code == 400

    # stdio without command.
    res = client.post(
        "/api/agents/default/mcp",
        json={"server_id": "bad", "transport": "stdio"},
    )
    assert res.status_code == 400

    # sse without url.
    res = client.post(
        "/api/agents/default/mcp",
        json={"server_id": "bad", "transport": "sse"},
    )
    assert res.status_code == 400

    # Unknown agent → 404.
    res = client.get("/api/agents/nope/mcp")
    assert res.status_code == 404


def test_mcp_connection_degrades_gracefully(client):
    """No MCP SDK installed → test endpoint answers 200 with ok=false;
    the tools endpoint answers 503 with a clear message.  Neither may 500."""
    client.post(
        "/api/agents/default/mcp",
        json={"server_id": "fs", "transport": "stdio", "command": "echo hi"},
    )

    res = client.post("/api/agents/default/mcp/fs/test")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert data["tools"] == []
    assert data["error"]

    res = client.get("/api/agents/default/mcp/fs/tools")
    assert res.status_code in (502, 503)
    assert res.json()["detail"]


def test_mcp_factory_degradation(tmp_path, monkeypatch):
    """MCP helpers skip tools silently when deps are missing."""
    from agentcore.runtime.mcp_client import load_mcp_tools_blocking

    monkeypatch.chdir(tmp_path)
    tools = load_mcp_tools_blocking(
        {"fs": {"server_id": "fs", "transport": "stdio", "command": "echo", "enabled": True}}
    )
    assert tools == []

    # Disabled servers short-circuit even earlier.
    assert load_mcp_tools_blocking({"fs": {"enabled": False}}) == []

    # Workspace read/write round-trip.
    from agentcore.runtime.workspace import Workspace

    ws = Workspace(workspace_id="t1", agent_id="t1")
    assert ws.read_mcp_config() == {}
    ws.write_mcp_config({"a": {"enabled": True}})
    assert ws.read_mcp_config() == {"a": {"enabled": True}}


# ---------------------------------------------------------------------------
# Skill pool & installation
# ---------------------------------------------------------------------------


def test_skill_pool_seeded_on_startup(client):
    res = client.get("/api/skills/pool")
    assert res.status_code == 200
    names = {s["dir_name"] for s in res.json()["skills"]}
    assert {"translator", "weekly-report"} <= names
    for skill in res.json()["skills"]:
        assert skill["description"]


def test_skill_pool_crud(client):
    # Create.
    res = client.post(
        "/api/skills/pool",
        json={
            "name": "code-review",
            "description": "代码审查技能",
            "content": "# 代码审查\n\n## 何时使用\n\n- 用户请求 review 时。\n",
        },
    )
    assert res.status_code == 200

    # Duplicate → 409.
    res = client.post(
        "/api/skills/pool",
        json={"name": "code-review", "description": "dup"},
    )
    assert res.status_code == 409

    # Invalid name → 400.
    res = client.post(
        "/api/skills/pool", json={"name": "Bad Name", "description": "x"}
    )
    assert res.status_code == 400

    # Read — frontmatter generated.
    res = client.get("/api/skills/pool/code-review")
    assert res.status_code == 200
    data = res.json()
    assert data["description"] == "代码审查技能"
    assert "name: code-review" in data["content"]
    assert "# 代码审查" in data["content"]

    # Update description only (body preserved).
    res = client.put(
        "/api/skills/pool/code-review", json={"description": "审查代码的技能"}
    )
    assert res.status_code == 200
    res = client.get("/api/skills/pool/code-review")
    assert res.json()["description"] == "审查代码的技能"
    assert "# 代码审查" in res.json()["content"]

    # Delete.
    res = client.delete("/api/skills/pool/code-review")
    assert res.status_code == 200
    res = client.get("/api/skills/pool/code-review")
    assert res.status_code == 404


def test_skill_install_and_uninstall(client):
    agent = "default"

    # Installed skills initially include the workspace example skill.
    res = client.get(f"/api/skills/agents/{agent}")
    assert res.status_code == 200
    installed = {s["dir_name"] for s in res.json()["skills"]}
    assert "example" in installed

    # Install a pool skill.
    res = client.post(f"/api/skills/pool/translator/install/{agent}")
    assert res.status_code == 200

    # File copied into the agent workspace.
    target = Path(".agentcore/workspace/agent/default/skills/translator/SKILL.md")
    assert target.exists()
    assert "name: translator" in target.read_text(encoding="utf-8")

    res = client.get(f"/api/skills/agents/{agent}")
    installed = {s["dir_name"] for s in res.json()["skills"]}
    assert "translator" in installed

    # Re-install is idempotent (overwrite).
    assert client.post(f"/api/skills/pool/translator/install/{agent}").status_code == 200

    # Uninstall removes only the workspace copy.
    res = client.delete(f"/api/skills/agents/{agent}/translator")
    assert res.status_code == 200
    assert not target.exists()
    assert (Path(".agentcore/skill_pool/translator/SKILL.md")).exists()

    # Unknown skill install → 404.
    res = client.post(f"/api/skills/pool/ghost/install/{agent}")
    assert res.status_code == 404
