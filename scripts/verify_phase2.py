"""End-to-end verification for Task #35 phase 2 (temporary script)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="agentcore_verify_"))
os.chdir(tmp)
os.environ.setdefault("AGENTCORE_LLM_API_KEY", "sk-verify-dummy")

from fastapi.testclient import TestClient  # noqa: E402

from agentcore.api import app  # noqa: E402
from agentcore.runtime import agent_factory as af_mod  # noqa: E402
from agentcore.runtime import chat_router  # noqa: E402
from agentcore.runtime.agent_factory import AgentFactory  # noqa: E402

with TestClient(app) as client:
    # 1) Create two agents.
    assert client.post("/api/agents", json={"agent_id": "alice"}).status_code == 200
    assert client.post("/api/agents", json={"agent_id": "bob"}).status_code == 200

    manager = client.app.state.agent_manager
    store = client.app.state.store

    # Enable subagents on alice only.
    ws_alice = manager.get_workspace("alice")
    cfg = ws_alice.read_agent_config()
    cfg["settings"]["enable_subagents"] = True
    ws_alice.write_agent_config(cfg)

    # 2) Capture create_deep_agent kwargs when resolving alice's graph.
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return object()

    orig = af_mod.create_deep_agent
    af_mod.create_deep_agent = fake_create
    orig_cp = chat_router.get_checkpointer
    chat_router.get_checkpointer = lambda: object()
    try:
        chat_router._agent_graph_cache.clear()
        chat_router._resolve_agent_graph(
            "alice", ws_alice, factory=AgentFactory(), manager=manager
        )
    finally:
        af_mod.create_deep_agent = orig
        chat_router.get_checkpointer = orig_cp

    subagents = captured.get("subagents")
    assert subagents, "subagents not injected!"
    names = [s["name"] for s in subagents]
    assert "alice" not in names, names  # never injects itself
    assert "bob" in names, names  # fresh installs also auto-create 'default'
    assert subagents[0]["system_prompt"], "empty system prompt"
    print(f"[OK] subagent injection: alice sees {names}")

    # 3) Session persistence layout: per-session files + index.
    store.save_session(
        "sess-1",
        {"session_id": "sess-1", "agent_id": "alice", "messages": []},
    )
    store.save_session(
        "sess-2",
        {"session_id": "sess-2", "agent_id": "bob", "messages": []},
    )
    data_dir = Path(".agentcore/data")
    sessions_root = data_dir / "sessions"
    assert (sessions_root / "sess-1.json").exists(), list(data_dir.iterdir())
    assert (sessions_root / "sess-2.json").exists()
    index = json.loads(
        (sessions_root / "sessions_index.json").read_text("utf-8")
    )
    assert set(index) == {"sess-1", "sess-2"}
    assert not (data_dir / "sessions.json").exists()
    print("[OK] sessions dir layout:", sorted(p.name for p in sessions_root.iterdir()))

    # 4) ChatManager per-session layout.
    cm = ws_alice.chat_manager
    cm.create_session("cs1", agent_id="alice")
    cm.add_message("cs1", "user", "hello")
    sessions_dir = ws_alice.sessions_dir
    assert (sessions_dir / "cs1.json").exists()
    assert (sessions_dir / "index.json").exists()
    print("[OK] ChatManager per-session files:", sorted(p.name for p in sessions_dir.iterdir()))

print("ALL VERIFICATIONS PASSED")
