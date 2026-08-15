"""Streamed-text normalisation tests.

Qwen-style streaming emits one chunk per token with a trailing newline, so
naive concatenation stores replies where each word sits on its own line.
``_normalize_assistant_text`` collapses those back into spaces while keeping
real paragraphs (blank-line separated).  The chat history endpoint also
cleans legacy data on read without rewriting the stored session.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentcore.api import app
from agentcore.runtime.chat_router import _normalize_assistant_text


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client running inside an isolated (temporary) data directory."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


# -- pure function -----------------------------------------------------------


def test_collapses_word_boundary_newlines() -> None:
    text = "Hello\nworld\nthis\nis\na\ntest"
    assert _normalize_assistant_text(text) == "Hello world this is a test"


def test_qwen_style_token_chunks() -> None:
    # Every token chunk ends with \n, so every word lands on its own line.
    text = "今天\n天气\n很\n好\n适合\n散步\n"
    assert _normalize_assistant_text(text) == "今天 天气 很 好 适合 散步"


def test_preserves_real_paragraphs() -> None:
    text = "First paragraph\n\nSecond paragraph"
    assert _normalize_assistant_text(text) == "First paragraph\n\nSecond paragraph"


def test_clamps_runaway_newlines() -> None:
    assert _normalize_assistant_text("one\n\n\n\n\ntwo") == "one\n\ntwo"


def test_strips_surrounding_newlines() -> None:
    assert _normalize_assistant_text("\n\nHello\nworld\n\n") == "Hello world"


def test_mixed_single_and_blank_lines() -> None:
    text = "Line one\nLine two\n\n\nLine three\nLine four\n"
    assert (
        _normalize_assistant_text(text)
        == "Line one Line two\n\nLine three Line four"
    )


def test_empty_and_plain_text() -> None:
    assert _normalize_assistant_text("") == ""
    assert _normalize_assistant_text("no newlines here") == "no newlines here"


# -- chat history endpoint (legacy data cleaned on read) ---------------------


def _seed_session(client: TestClient, session_id: str) -> None:
    client.post("/api/agents", json={"agent_id": "demo"})
    store = client.app.state.store
    store.save_session(
        session_id,
        {
            "session_id": session_id,
            "agent_id": "demo",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:01Z",
            "messages": [
                {
                    "role": "user",
                    "content": "hi",
                    "timestamp": "2026-01-01T00:00:00Z",
                },
                {
                    "role": "assistant",
                    "content": "Hello\nworld\n",
                    "reasoning": "Step\none\nStep\ntwo\n",
                    "timestamp": "2026-01-01T00:00:01Z",
                },
            ],
        },
    )


def test_history_cleans_legacy_streamed_data(client) -> None:
    _seed_session(client, "sess-legacy-1")

    history = client.get(
        "/api/chat/history", params={"session_id": "sess-legacy-1"}
    ).json()

    assert len(history) == 2
    assert history[0]["content"] == "hi"  # user message untouched
    assert history[1]["content"] == "Hello world"
    assert history[1]["reasoning"] == "Step one Step two"


def test_history_does_not_rewrite_stored_data(client) -> None:
    _seed_session(client, "sess-legacy-2")

    client.get("/api/chat/history", params={"session_id": "sess-legacy-2"})

    stored = client.app.state.store.load_session("sess-legacy-2")
    assert stored["messages"][1]["content"] == "Hello\nworld\n"
    assert stored["messages"][1]["reasoning"] == "Step\none\nStep\ntwo\n"
