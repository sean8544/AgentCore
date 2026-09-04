"""Tests for the session trace endpoint (LangSmith-style step timeline).

The endpoint rebuilds a per-step execution timeline read-only from the
local checkpoints database.  These tests build a small checkpoints.db
directly (same schema + serializer the runtime uses) in an isolated temp
data dir, then assert the trace endpoint returns ordered steps with
model/tool events.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agentcore.api import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client inside an isolated (temporary) data directory."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def _build_checkpoints_db(data_dir, session_id: str) -> None:
    """Insert two checkpoint rows + message writes for *session_id*.

    Mirrors what LangGraph's SqliteSaver writes: msgpack blob with a
    ``ts`` field, JSON metadata carrying ``step``/``source``, and
    serialized message deltas in the ``writes`` table.
    """
    import sqlite3

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    db = data_dir / ".agentcore" / "checkpoints.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    serde = JsonPlusSerializer()

    def ckpt_blob(ts: str) -> bytes:
        payload = {
            "v": 1,
            "ts": ts,
            "id": "checkpoint-1",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
            "pending_writes": [],
        }
        return serde.dumps_typed(payload)[1]

    def write_blob(msg) -> bytes:
        return serde.dumps_typed(msg)[1]

    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL,
            checkpoint_id TEXT NOT NULL,
            parent_checkpoint_id TEXT,
            type TEXT,
            checkpoint BLOB,
            metadata BLOB,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        );
        CREATE TABLE IF NOT EXISTS writes (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL,
            checkpoint_id TEXT NOT NULL,
            task_id TEXT,
            idx INTEGER,
            channel TEXT,
            type TEXT,
            value BLOB,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        );
        """
    )

    # step -1: input checkpoint + human message write
    con.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, "
        "type, checkpoint, metadata) VALUES (?, '', 'cp-input', 'msgpack', ?, ?)",
        (
            session_id,
            ckpt_blob("2026-08-26T10:00:00.000000+00:00"),
            json.dumps({"source": "input", "step": -1, "parents": {}}),
        ),
    )
    con.execute(
        "INSERT INTO writes (thread_id, checkpoint_ns, checkpoint_id, "
        "task_id, idx, channel, type, value) VALUES (?, '', 'cp-input', "
        "'t1', 0, 'messages', 'msgpack', ?)",
        (session_id, write_blob(HumanMessage(content="请检查 /tmp 目录"))),
    )

    # step 0: model turn deciding to call ls
    con.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, "
        "type, checkpoint, metadata) VALUES (?, '', 'cp-0', 'msgpack', ?, ?)",
        (
            session_id,
            ckpt_blob("2026-08-26T10:00:01.000000+00:00"),
            json.dumps({"source": "loop", "step": 0, "parents": {}}),
        ),
    )
    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "ls", "args": {"path": "/tmp"}, "id": "call-1", "type": "tool_call"}
        ],
    )
    con.execute(
        "INSERT INTO writes (thread_id, checkpoint_ns, checkpoint_id, "
        "task_id, idx, channel, type, value) VALUES (?, '', 'cp-0', "
        "'t2', 0, 'messages', 'msgpack', ?)",
        (session_id, write_blob(ai)),
    )

    # step 1: tool result + final answer
    con.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, "
        "type, checkpoint, metadata) VALUES (?, '', 'cp-1', 'msgpack', ?, ?)",
        (
            session_id,
            ckpt_blob("2026-08-26T10:00:02.000000+00:00"),
            json.dumps({"source": "loop", "step": 1, "parents": {}}),
        ),
    )
    tool_msg = ToolMessage(content='["a.txt", "b.log"]', tool_call_id="call-1", name="ls")
    con.execute(
        "INSERT INTO writes (thread_id, checkpoint_ns, checkpoint_id, "
        "task_id, idx, channel, type, value) VALUES (?, '', 'cp-1', "
        "'t3', 0, 'messages', 'msgpack', ?)",
        (session_id, write_blob(tool_msg)),
    )
    con.execute(
        "INSERT INTO writes (thread_id, checkpoint_ns, checkpoint_id, "
        "task_id, idx, channel, type, value) VALUES (?, '', 'cp-1', "
        "'t4', 1, 'messages', 'msgpack', ?)",
        (
            session_id,
            write_blob(AIMessage(content="检查完成，/tmp 下有两个文件。")),
        ),
    )
    con.commit()
    con.close()


def test_trace_404_for_unknown_session(client) -> None:
    """GET /api/chat/sessions/{id}/trace returns 404 for unknown sessions."""
    resp = client.get("/api/chat/sessions/unknown-session-xyz/trace")
    assert resp.status_code == 404


def test_trace_empty_without_checkpoint_data(client, tmp_path) -> None:
    """A session with no checkpoint rows yields an empty timeline."""
    session_id = "trace-empty-session"
    store = client.app.state.store
    store.save_session(session_id, {
        "session_id": session_id,
        "agent_id": "demo-agent",
        "created_at": "2026-08-26T10:00:00+00:00",
        "updated_at": "2026-08-26T10:00:00+00:00",
        "message_count": 0,
        "messages": [],
    })
    # No checkpoints.db exists in the isolated data dir.
    resp = client.get(f"/api/chat/sessions/{session_id}/trace")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session_id
    assert body["steps"] == []


def test_trace_rebuilds_step_timeline(client, tmp_path) -> None:
    """Trace returns ordered steps with model/tool events from checkpoints."""
    session_id = "trace-demo-session"
    store = client.app.state.store
    store.save_session(session_id, {
        "session_id": session_id,
        "agent_id": "demo-agent",
        "created_at": "2026-08-26T10:00:00+00:00",
        "updated_at": "2026-08-26T10:00:02+00:00",
        "message_count": 3,
        "messages": [],
    })
    _build_checkpoints_db(tmp_path, session_id)

    resp = client.get(f"/api/chat/sessions/{session_id}/trace")
    assert resp.status_code == 200
    steps = resp.json()["steps"]
    assert [s["step"] for s in steps] == [-1, 0, 1]

    # step -1: human input
    assert steps[0]["source"] == "input"
    assert steps[0]["ts"] == "2026-08-26T10:00:00.000000+00:00"
    assert steps[0]["events"][0]["kind"] == "human"
    assert "请检查 /tmp" in steps[0]["events"][0]["content"]

    # step 0: ai event with tool call
    ai_ev = steps[1]["events"][0]
    assert ai_ev["kind"] == "ai"
    assert ai_ev["tool_calls"][0]["name"] == "ls"
    assert ai_ev["tool_calls"][0]["args"] == {"path": "/tmp"}

    # step 1: tool result then final answer, in write order
    kinds = [e["kind"] for e in steps[2]["events"]]
    assert kinds == ["tool", "ai"]
    tool_ev = steps[2]["events"][0]
    assert tool_ev["name"] == "ls"
    assert tool_ev["tool_call_id"] == "call-1"
    assert '["a.txt", "b.log"]' in tool_ev["content"]
    assert steps[2]["events"][1]["content"] == "检查完成，/tmp 下有两个文件。"