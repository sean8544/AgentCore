"""Assistant-text fidelity tests.

Streamed replies arrive as *deltas*, so they must be concatenated verbatim
when persisted — that is the only way a reloaded session can render exactly
like the live answer.

The previous implementation joined deltas with ``\\n`` and then collapsed
every single newline into a space.  That injected a space at each token
boundary (mangling CJK replies, numbers and e-mails) and broke inline
markdown, e.g. ``**粗体**`` became ``** 粗体 **`` and rendered literally.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentcore.api import app
from agentcore.runtime.chat_router import (
    _append_assistant_turn,
    _collapse_blank_runs,
    _extract_text_from_result,
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Test client running inside an isolated (temporary) data directory."""
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def _seed_session(
    client: TestClient, session_id: str, messages: list[dict]
) -> None:
    client.post("/api/agents", json={"agent_id": "demo"})
    client.app.state.store.save_session(
        session_id,
        {
            "session_id": session_id,
            "agent_id": "demo",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:01Z",
            "messages": messages,
        },
    )


# -- blank-run helper --------------------------------------------------------


def test_collapse_keeps_single_newlines() -> None:
    """Markdown line breaks (list items, table rows) must survive."""
    text = "- 第一项\n- 第二项\n\n\n结尾"
    assert _collapse_blank_runs(text) == "- 第一项\n- 第二项\n\n结尾"


def test_collapse_strips_and_clamps_blank_runs() -> None:
    assert _collapse_blank_runs("\n\none\n\n\n\ntwo\n\n") == "one\n\ntwo"


def test_collapse_is_noop_for_plain_text() -> None:
    assert _collapse_blank_runs("no newlines here") == "no newlines here"
    assert _collapse_blank_runs("") == ""


# -- streaming persistence (the regression) ----------------------------------


def test_stream_deltas_are_persisted_verbatim(client) -> None:
    _seed_session(client, "sess-deltas", [])
    store = client.app.state.store
    deltas = [
        "已", "通过", "知识库", "查询", "到", "结果", "：", "\n", "\n",
        "##", " ", "如何申请", "福利", "\n", "\n",
        "**", "一", "、", "资格条件", "**", "\n", "\n",
        "-", " ", "员工", "\n", "-", " ", "试用期",
    ]
    _append_assistant_turn(
        store, "sess-deltas", deltas, ["思", "考"], [], None, [], None
    )

    msg = store.load_session("sess-deltas")["messages"][-1]
    assert msg["content"] == "".join(deltas)
    # Inline emphasis and list structure stay intact (no injected spaces).
    assert "**一、资格条件**" in msg["content"]
    assert "- 员工\n- 试用期" in msg["content"]
    assert msg["reasoning"] == "思考"


def test_no_space_injected_between_cjk_deltas(client) -> None:
    _seed_session(client, "sess-cjk", [])
    _append_assistant_turn(
        client.app.state.store, "sess-cjk", ["今天", "天气", "很好"], [], [], None, [], None
    )

    stored = client.app.state.store.load_session("sess-cjk")["messages"][-1]
    assert stored["content"] == "今天天气很好"


# -- history endpoint --------------------------------------------------------


def test_history_endpoint_returns_stored_text_untouched(client) -> None:
    content = "前言\n\n## 标题\n- 项目一\n- 项目二\n\n**加粗**结尾"
    _seed_session(
        client,
        "sess-history",
        [
            {"role": "user", "content": "hi", "timestamp": "2026-01-01T00:00:00Z"},
            {
                "role": "assistant",
                "content": content,
                "reasoning": "第一 步\n第二步",
                "timestamp": "2026-01-01T00:00:01Z",
            },
        ],
    )

    history = client.get(
        "/api/chat/history", params={"session_id": "sess-history"}
    ).json()

    assert len(history) == 2
    assert history[0]["content"] == "hi"
    assert history[1]["content"] == content
    assert history[1]["reasoning"] == "第一 步\n第二步"


def test_history_endpoint_does_not_rewrite_stored_data(client) -> None:
    raw = "Hello\nworld\n"
    _seed_session(
        client,
        "sess-readonly",
        [{"role": "assistant", "content": raw, "timestamp": "2026-01-01T00:00:01Z"}],
    )

    client.get("/api/chat/history", params={"session_id": "sess-readonly"})

    stored = client.app.state.store.load_session("sess-readonly")
    assert stored["messages"][0]["content"] == raw


# -- non-streaming result extraction -----------------------------------------


def test_extract_text_separates_ai_messages_with_blank_line() -> None:
    result = {
        "messages": [
            {"type": "human", "content": "问题"},
            {"type": "ai", "content": "我先查一下"},
            {"type": "tool", "content": "工具输出"},
            {"type": "ai", "content": "- 项目一\n- 项目二"},
        ]
    }

    text, calls = _extract_text_from_result(result)

    assert text == "我先查一下\n\n- 项目一\n- 项目二"
    assert calls == []
