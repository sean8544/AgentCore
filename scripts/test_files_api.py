"""Smoke test for the workspace file management API (/api/agents/{id}/files/*).

Run from the repository root:

    python scripts/test_files_api.py
"""

from __future__ import annotations

import asyncio
import io

from fastapi.testclient import TestClient

from agentcore import api

AGENT_ID = "smoke-files"


def main() -> None:
    # Create a workspace for the smoke agent (module-level manager is
    # mounted on app.state, so no lifespan needed for TestClient).
    manager = api.app.state.agent_manager
    asyncio.run(manager.get_or_create_workspace(AGENT_ID))

    client = TestClient(api.app)
    base = f"/api/agents/{AGENT_ID}/files"

    # 1. Tree listing — kernel files seeded by the workspace must exist.
    resp = client.get(f"{base}/tree")
    assert resp.status_code == 200, resp.text
    names = {item["name"] for item in resp.json()["items"]}
    assert {"agent.md", "profile.md", "soul.md"} <= names, names
    print("tree listing OK:", sorted(names))

    # 2. Write + read a regular file.
    resp = client.put(f"{base}/content", params={"path": "notes/hello.txt"}, json={"content": "hello world"})
    assert resp.status_code == 200, resp.text
    resp = client.get(f"{base}/content", params={"path": "notes/hello.txt"})
    assert resp.status_code == 200 and resp.json()["content"] == "hello world", resp.text
    print("write/read OK")

    # 3. Write a kernel file via the files API → graph cache invalidated.
    resp = client.put(f"{base}/content", params={"path": "soul.md"}, json={"content": "# 核心人设\n\n测试更新。"})
    assert resp.status_code == 200, resp.text
    resp = client.get(f"/api/agents/{AGENT_ID}/kernel/soul.md")
    assert resp.status_code == 200 and "测试更新" in resp.json()["content"], resp.text
    print("kernel file write-through OK")

    # 4. Upload.
    resp = client.post(
        f"{base}/upload",
        params={"path": "notes"},
        files={"file": ("data.bin", io.BytesIO(b"\xff\xd8\xff\xe0jpeg-ish"), "application/octet-stream")},
    )
    assert resp.status_code == 200 and resp.json()["path"] == "notes/data.bin", resp.text
    print("upload OK:", resp.json())

    # 5. Download.
    resp = client.get(f"{base}/download", params={"path": "notes/data.bin"})
    assert resp.status_code == 200 and resp.content == b"\xff\xd8\xff\xe0jpeg-ish", resp.text
    print("download OK")

    # 6. Binary file rejected for text editing.
    resp = client.get(f"{base}/content", params={"path": "notes/data.bin"})
    assert resp.status_code == 400, resp.text
    print("binary rejection OK")

    # 7. Path traversal blocked.
    for evil in ("../../pyproject.toml", "..\\..\\pyproject.toml", "notes/../../../etc/passwd"):
        resp = client.get(f"{base}/content", params={"path": evil})
        assert resp.status_code in (403, 404), f"{evil} -> {resp.status_code}: {resp.text}"
    resp = client.put(f"{base}/content", params={"path": "../escape.txt"}, json={"content": "x"})
    assert resp.status_code == 403, resp.text
    print("path traversal protection OK")

    # 8. Unknown agent → 404.
    resp = client.get("/api/agents/no-such-agent/files/tree")
    assert resp.status_code == 404, resp.text
    print("unknown agent 404 OK")

    print("\nAll file API smoke tests passed.")


if __name__ == "__main__":
    main()
