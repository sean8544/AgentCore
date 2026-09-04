"""Chat attachment upload tests.

Covers:

* ``POST /api/chat/upload`` — files land in the agent workspace's
  ``uploads/`` dir with a uuid prefix, size cap enforced, unknown agents
  rejected, file names sanitised
* ``ChatRequest.attachments`` — persisted as user-message extras and
  injected into the model input as a workspace-path note
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agentcore.api import app
from agentcore.runtime import chat_router, paths


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client running inside an isolated (temporary) data directory."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


class _CapturingGraph:
    """Stand-in compiled graph that records the invocation input."""

    def __init__(self) -> None:
        self.inputs: list[dict] = []

    async def ainvoke(self, input_data, config=None):
        self.inputs.append(input_data)
        return {"messages": [AIMessage(content="已完成")]}


# ---------------------------------------------------------------------------
# POST /api/chat/upload
# ---------------------------------------------------------------------------


def test_upload_stores_file_in_workspace(client) -> None:
    client.post("/api/agents", json={"agent_id": "up-demo"})

    resp = client.post(
        "/api/chat/upload",
        params={"agent_id": "up-demo"},
        files={"file": ("report.txt", b"hello world", "text/plain")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "report.txt"
    assert body["size"] == len(b"hello world")
    assert body["path"].startswith("uploads/")
    # uuid prefix avoids collisions between repeated uploads.
    assert body["path"] != "uploads/report.txt"

    stored = paths.get_agent_workspace_dir("up-demo") / body["path"]
    assert stored.is_file()
    assert stored.read_bytes() == b"hello world"


def test_upload_rejects_unknown_agent(client) -> None:
    resp = client.post(
        "/api/chat/upload",
        params={"agent_id": "ghost"},
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 404


def test_upload_enforces_size_limit(client, monkeypatch) -> None:
    client.post("/api/agents", json={"agent_id": "up-limit"})
    monkeypatch.setattr(chat_router, "MAX_CHAT_UPLOAD_BYTES", 8)

    resp = client.post(
        "/api/chat/upload",
        params={"agent_id": "up-limit"},
        files={"file": ("big.bin", b"0123456789", "application/octet-stream")},
    )
    assert resp.status_code == 413


def test_safe_upload_filename_sanitises() -> None:
    assert chat_router._safe_upload_filename("../evil.txt") == "evil.txt"
    assert chat_router._safe_upload_filename("a\\b\\c.txt") == "c.txt"
    assert chat_router._safe_upload_filename("we?rd na me.md") == "we_rd_na_me.md"
    assert chat_router._safe_upload_filename("") == "file"
    assert chat_router._safe_upload_filename("x" * 300).count("x") == 120


# ---------------------------------------------------------------------------
# Attachments on chat requests
# ---------------------------------------------------------------------------


def test_attachment_note_and_extras_helpers() -> None:
    assert chat_router._with_attachment_note("hi", []) == "hi"

    atts = [chat_router.Attachment(name="a.txt", path="uploads/u_a.txt", size=3)]
    note = chat_router._with_attachment_note("hi", atts)
    assert note.startswith("hi")
    assert "a.txt -> uploads/u_a.txt" in note

    extras = chat_router._attachment_extras(atts)
    assert extras == {
        "attachments": [{"name": "a.txt", "path": "uploads/u_a.txt", "size": 3}]
    }
    assert chat_router._attachment_extras([]) is None


def test_sync_chat_persists_and_injects_attachments(client) -> None:
    client.post("/api/agents", json={"agent_id": "att-demo"})
    graph = _CapturingGraph()
    client.app.state.chat_state.graph_cache["att-demo"] = graph

    attachments = [{"name": "report.txt", "path": "uploads/u_report.txt", "size": 11}]
    resp = client.post(
        "/api/chat",
        json={
            "agent_id": "att-demo",
            "message": "帮我总结这个文件",
            "attachments": attachments,
        },
    )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    # The model input carries the workspace-path note.
    sent = graph.inputs[0]["messages"][0]
    assert "帮我总结这个文件" in sent.content
    assert "report.txt -> uploads/u_report.txt" in sent.content

    # Session history keeps the original text plus attachment metadata.
    store = client.app.state.store
    messages = store.load_session(session_id)["messages"]
    user_msg = messages[0]
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "帮我总结这个文件"
    assert user_msg["attachments"] == attachments


def test_sync_chat_without_attachments_unchanged(client) -> None:
    client.post("/api/agents", json={"agent_id": "att-none"})
    graph = _CapturingGraph()
    client.app.state.chat_state.graph_cache["att-none"] = graph

    resp = client.post(
        "/api/chat",
        json={"agent_id": "att-none", "message": "你好"},
    )
    assert resp.status_code == 200

    sent = graph.inputs[0]["messages"][0]
    assert sent.content == "你好"

    store = client.app.state.store
    messages = store.load_session(resp.json()["session_id"])["messages"]
    assert "attachments" not in messages[0]
