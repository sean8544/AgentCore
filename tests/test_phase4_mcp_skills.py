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
    assert {"translator", "weekly-report", "create-skill", "aio-sandbox-user-guide"} <= names
    for skill in res.json()["skills"]:
        assert skill["description"]


def test_skill_pool_seed_supports_multi_file_sample(client):
    """aio-sandbox-user-guide ships reference.md — seeding must copy the
    whole tree, not just SKILL.md."""
    res = client.get("/api/skills/pool/aio-sandbox-user-guide/files")
    assert res.status_code == 200
    paths_ = {f["path"] for f in res.json()["files"]}
    assert {"SKILL.md", "reference.md"} <= paths_


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


def test_skill_enable_disable_toggle(client):
    agent = "default"
    client.post(f"/api/skills/pool/translator/install/{agent}")

    # Enabled by default after install.
    res = client.get(f"/api/skills/agents/{agent}")
    states = {s["dir_name"]: s["enabled"] for s in res.json()["skills"]}
    assert states["translator"] is True

    # Disable → persisted into agent.json skills.disabled.
    res = client.put(
        f"/api/skills/agents/{agent}/translator", json={"enabled": False}
    )
    assert res.status_code == 200
    assert res.json()["enabled"] is False
    agent_json = json.loads(
        Path(".agentcore/workspace/agent/default/agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert "translator" in agent_json["skills"]["disabled"]

    res = client.get(f"/api/skills/agents/{agent}")
    states = {s["dir_name"]: s["enabled"] for s in res.json()["skills"]}
    assert states["translator"] is False

    # Reading the installed copy returns content + enabled state.
    res = client.get(f"/api/skills/agents/{agent}/translator")
    assert res.status_code == 200
    data = res.json()
    assert data["enabled"] is False
    assert "name: translator" in data["content"]

    # Re-enable.
    res = client.put(
        f"/api/skills/agents/{agent}/translator", json={"enabled": True}
    )
    assert res.status_code == 200
    agent_json = json.loads(
        Path(".agentcore/workspace/agent/default/agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert agent_json["skills"]["disabled"] == []

    # Toggling a skill that is not installed → 404.
    res = client.put(
        f"/api/skills/agents/{agent}/ghost", json={"enabled": False}
    )
    assert res.status_code == 404


def test_skill_reinstall_reenables_and_uninstall_cleans_state(client):
    agent = "default"
    client.post(f"/api/skills/pool/translator/install/{agent}")
    client.put(f"/api/skills/agents/{agent}/translator", json={"enabled": False})

    # Reinstall flips the skill back to enabled.
    res = client.post(f"/api/skills/pool/translator/install/{agent}")
    assert res.status_code == 200
    res = client.get(f"/api/skills/agents/{agent}")
    states = {s["dir_name"]: s["enabled"] for s in res.json()["skills"]}
    assert states["translator"] is True

    # Disable again, then uninstall — the stale disable entry is cleaned.
    client.put(f"/api/skills/agents/{agent}/translator", json={"enabled": False})
    assert client.delete(f"/api/skills/agents/{agent}/translator").status_code == 200
    agent_json = json.loads(
        Path(".agentcore/workspace/agent/default/agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert agent_json["skills"]["disabled"] == []


def test_skill_batch_install_to_multiple_agents(client):
    # Create a second agent.
    res = client.post("/api/agents", json={"agent_id": "helper"})
    assert res.status_code == 200

    res = client.post(
        "/api/skills/pool/translator/install/default",
        json={"agent_ids": ["helper"]},
    )
    assert res.status_code == 200
    assert set(res.json()["agent_ids"]) == {"default", "helper"}

    assert Path(
        ".agentcore/workspace/agent/default/skills/translator/SKILL.md"
    ).exists()
    assert Path(
        ".agentcore/workspace/agent/helper/skills/translator/SKILL.md"
    ).exists()

    # Unknown agent in the batch → 404.
    res = client.post(
        "/api/skills/pool/translator/install/default",
        json={"agent_ids": ["ghost"]},
    )
    assert res.status_code == 404


def test_skill_pool_sync_propagates_to_installed_agents(client):
    agent = "default"
    client.post(f"/api/skills/pool/translator/install/{agent}")

    # Edit the pool version.
    res = client.put(
        "/api/skills/pool/translator",
        json={"content": "# 翻译技能 v2\n\n更新的正文。\n"},
    )
    assert res.status_code == 200

    # Workspace copy is still stale before sync.
    target = Path(".agentcore/workspace/agent/default/skills/translator/SKILL.md")
    assert "翻译技能 v2" not in target.read_text(encoding="utf-8")

    # Sync pushes the pool content to every installed copy.
    res = client.post("/api/skills/pool/translator/sync")
    assert res.status_code == 200
    assert res.json()["synced_agents"] == [agent]
    assert "翻译技能 v2" in target.read_text(encoding="utf-8")

    # Disable state survives a sync.
    client.put(f"/api/skills/agents/{agent}/translator", json={"enabled": False})
    assert client.post("/api/skills/pool/translator/sync").status_code == 200
    res = client.get(f"/api/skills/agents/{agent}")
    states = {s["dir_name"]: s["enabled"] for s in res.json()["skills"]}
    assert states["translator"] is False

    # Syncing an unknown skill → 404.
    assert client.post("/api/skills/pool/ghost/sync").status_code == 404


def test_skill_exclusion_middleware_filters_metadata():
    from agentcore.runtime.agent_factory import _SkillExclusionMiddleware

    mw = _SkillExclusionMiddleware(disabled=frozenset({"translator"}))
    state = {
        "skills_metadata": [
            {"name": "translator", "description": "x", "path": "/skills/translator/SKILL.md"},
            {"name": "example", "description": "y", "path": "/skills/example/SKILL.md"},
        ]
    }
    update = mw.before_agent(state, runtime=None, config=None)
    assert update is not None
    assert [s["name"] for s in update["skills_metadata"]] == ["example"]

    # Nothing disabled → no update needed.
    mw_none = _SkillExclusionMiddleware(disabled=frozenset())
    # Empty metadata list → untouched.
    assert mw._filtered_update({}) is None
    assert mw_none.before_agent(state, runtime=None, config=None) is None


# ---------------------------------------------------------------------------
# Skill file management (nested files, zip upload)
# ---------------------------------------------------------------------------


def test_pool_skill_files_list_and_read(client):
    """List files and read SKILL.md via the file management endpoints."""
    res = client.get("/api/skills/pool/translator/files")
    assert res.status_code == 200
    files = res.json()["files"]
    paths = [f["path"] for f in files]
    assert "SKILL.md" in paths

    # Read the SKILL.md content
    res = client.get("/api/skills/pool/translator/files/content", params={"path": "SKILL.md"})
    assert res.status_code == 200
    assert "name: translator" in res.json()["content"]


def test_pool_skill_files_write_and_delete(client):
    """Create a nested file, verify it exists, then delete it."""
    skill = "translator"

    # Write a nested file
    res = client.put(
        f"/api/skills/pool/{skill}/files/content",
        json={"content": "# Template\n\nHello {{name}}"},
        params={"path": "templates/email.md"},
    )
    assert res.status_code == 200

    # Verify it shows up in the file list
    res = client.get(f"/api/skills/pool/{skill}/files")
    paths = [f["path"] for f in res.json()["files"]]
    assert "templates/email.md" in paths

    # Read it back
    res = client.get(f"/api/skills/pool/{skill}/files/content", params={"path": "templates/email.md"})
    assert res.status_code == 200
    assert "Hello {{name}}" in res.json()["content"]

    # Delete it
    res = client.delete(f"/api/skills/pool/{skill}/files", params={"path": "templates/email.md"})
    assert res.status_code == 200
    assert res.json()["kind"] == "file"

    # Delete the empty directory
    res = client.delete(f"/api/skills/pool/{skill}/files", params={"path": "templates", "recursive": True})
    assert res.status_code == 200
    assert res.json()["kind"] == "directory"


def test_pool_skill_file_upload(client):
    """Upload a regular file into a skill directory."""
    skill = "translator"

    res = client.post(
        f"/api/skills/pool/{skill}/files/upload",
        files={"file": ("helper.py", "print('hello')", "text/plain")},
        params={"path": "scripts"},
    )
    assert res.status_code == 200
    assert res.json()["path"] == "scripts/helper.py"

    # Verify it's readable
    res = client.get(f"/api/skills/pool/{skill}/files/content", params={"path": "scripts/helper.py"})
    assert res.status_code == 200
    assert "print('hello')" in res.json()["content"]

    # Cleanup
    client.delete(f"/api/skills/pool/{skill}/files", params={"path": "scripts", "recursive": True})


def test_pool_skill_zip_upload(client):
    """Upload a zip archive and verify extraction."""
    import io
    import zipfile

    skill = "translator"

    # Create a zip with nested files
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: translator\ndescription: updated\n---\n\n# Updated")
        zf.writestr("templates/prompt.txt", "Translate this: {text}")
        zf.writestr("examples/basic.json", '{"input": "hello", "output": "hola"}')
    buf.seek(0)

    res = client.post(
        f"/api/skills/pool/{skill}/files/upload",
        files={"file": ("skill.zip", buf, "application/zip")},
    )
    assert res.status_code == 200
    assert res.json()["count"] == 3

    # Verify files exist
    res = client.get(f"/api/skills/pool/{skill}/files")
    paths = {f["path"] for f in res.json()["files"]}
    assert "SKILL.md" in paths
    assert "templates/prompt.txt" in paths
    assert "examples/basic.json" in paths

    # Cleanup
    client.delete(f"/api/skills/pool/{skill}/files", params={"path": "templates", "recursive": True})
    client.delete(f"/api/skills/pool/{skill}/files", params={"path": "examples", "recursive": True})


def test_pool_skill_file_traversal_blocked(client):
    """Path traversal attempts must be rejected."""
    res = client.get(
        "/api/skills/pool/translator/files/content",
        params={"path": "../../etc/passwd"},
    )
    assert res.status_code == 403


def test_agent_skill_files_crud(client):
    """File management endpoints for agent-installed skills."""
    agent = "default"
    skill = "translator"

    # Install the skill first
    client.post(f"/api/skills/pool/{skill}/install/{agent}")

    # List files
    res = client.get(f"/api/skills/agents/{agent}/{skill}/files")
    assert res.status_code == 200
    paths = [f["path"] for f in res.json()["files"]]
    assert "SKILL.md" in paths

    # Write a nested file
    res = client.put(
        f"/api/skills/agents/{agent}/{skill}/files/content",
        json={"content": "example data"},
        params={"path": "data/sample.txt"},
    )
    assert res.status_code == 200

    # Read it back
    res = client.get(
        f"/api/skills/agents/{agent}/{skill}/files/content",
        params={"path": "data/sample.txt"},
    )
    assert res.status_code == 200
    assert "example data" in res.json()["content"]

    # Delete
    res = client.delete(
        f"/api/skills/agents/{agent}/{skill}/files",
        params={"path": "data/sample.txt"},
    )
    assert res.status_code == 200
    assert res.json()["kind"] == "file"


def test_skill_summary_includes_file_count(client):
    """Skill listing should include file_count."""
    res = client.get("/api/skills/pool")
    assert res.status_code == 200
    for skill in res.json()["skills"]:
        assert "file_count" in skill
        assert skill["file_count"] >= 1  # at least SKILL.md


# ---------------------------------------------------------------------------
# Pool bulk ZIP import
# ---------------------------------------------------------------------------


def test_pool_upload_zip_single_skill(client):
    """Upload a zip with a single skill directory."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("my-tool/SKILL.md", "---\nname: my-tool\ndescription: A test tool\n---\n\n# My Tool")
        zf.writestr("my-tool/templates/prompt.txt", "Hello {name}")
        zf.writestr("my-tool/assets/icon.png", "\x89PNG")
    buf.seek(0)

    res = client.post(
        "/api/skills/pool/upload-zip",
        files={"file": ("my-tool.zip", buf, "application/zip")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["imported_count"] == 1
    assert data["skipped_count"] == 0
    assert data["imported"][0]["dir_name"] == "my-tool"

    # Verify files exist
    res = client.get("/api/skills/pool/my-tool/files")
    paths = {f["path"] for f in res.json()["files"]}
    assert "SKILL.md" in paths
    assert "templates/prompt.txt" in paths
    assert "assets/icon.png" in paths

    # Cleanup
    client.delete("/api/skills/pool/my-tool")


def test_pool_upload_zip_multiple_skills(client):
    """Upload a zip with multiple skill directories."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("alpha-skill/SKILL.md", "---\nname: alpha-skill\ndescription: Alpha\n---\n\n# Alpha")
        zf.writestr("beta-skill/SKILL.md", "---\nname: beta-skill\ndescription: Beta\n---\n\n# Beta")
        zf.writestr("beta-skill/helper.py", "print('beta')")
    buf.seek(0)

    res = client.post(
        "/api/skills/pool/upload-zip",
        files={"file": ("skills.zip", buf, "application/zip")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["imported_count"] == 2
    imported_names = {s["dir_name"] for s in data["imported"]}
    assert imported_names == {"alpha-skill", "beta-skill"}

    # Cleanup
    client.delete("/api/skills/pool/alpha-skill")
    client.delete("/api/skills/pool/beta-skill")


def test_pool_upload_zip_nested_subdirs(client):
    """Zip with deeply nested subdirectories within a skill."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("deep-skill/SKILL.md", "---\nname: deep-skill\ndescription: Deep\n---\n")
        zf.writestr("deep-skill/a/b/c/data.json", '{"key": "value"}')
        zf.writestr("deep-skill/x/y/z.txt", "nested text")
    buf.seek(0)

    res = client.post(
        "/api/skills/pool/upload-zip",
        files={"file": ("deep.zip", buf, "application/zip")},
    )
    assert res.status_code == 200
    assert res.json()["imported_count"] == 1

    res = client.get("/api/skills/pool/deep-skill/files")
    paths = {f["path"] for f in res.json()["files"]}
    assert "a/b/c/data.json" in paths
    assert "x/y/z.txt" in paths

    # Cleanup
    client.delete("/api/skills/pool/deep-skill")


def test_pool_upload_zip_skips_existing(client):
    """Existing skills should be skipped, not overwritten."""
    import io
    import zipfile

    # 'translator' is seeded and already exists
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("translator/SKILL.md", "---\nname: translator\ndescription: dup\n---\n")
        zf.writestr("new-one/SKILL.md", "---\nname: new-one\ndescription: New\n---\n")
    buf.seek(0)

    res = client.post(
        "/api/skills/pool/upload-zip",
        files={"file": ("mix.zip", buf, "application/zip")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["imported_count"] == 1
    assert data["imported"][0]["dir_name"] == "new-one"
    assert "translator" in data["skipped"]

    # Cleanup
    client.delete("/api/skills/pool/new-one")


def test_pool_upload_zip_no_skill_md_rejected(client):
    """Zip without any SKILL.md should return 400."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "Just a text file")
        zf.writestr("data/info.json", '{"a": 1}')
    buf.seek(0)

    res = client.post(
        "/api/skills/pool/upload-zip",
        files={"file": ("noskill.zip", buf, "application/zip")},
    )
    assert res.status_code == 400
    assert "SKILL.md" in res.json()["detail"]
