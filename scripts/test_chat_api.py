"""Quick smoke test for the chat API.

Starts a local uvicorn server with the chat router mounted, then exercises
the synchronous chat endpoint and session management endpoints.

Usage::

    python scripts/test_chat_api.py

Requirements:
    - ``uvicorn`` installed in the current environment
    - At least one created agent (persisted agent state); the chat test
      will skip gracefully if none is available
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure the project root is on sys.path so ``agentcore`` is importable.
_project_root = Path(__file__).resolve().parent.parent
_src = _project_root / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import httpx
import uvicorn

from agentcore.repository import ControlPlaneStore
from agentcore.runtime.chat_router import router as chat_router

from fastapi import FastAPI


# ---------------------------------------------------------------------------
# App factory — minimal FastAPI app with only the chat router
# ---------------------------------------------------------------------------

def _create_test_app() -> FastAPI:
    """Build a minimal FastAPI app wired to the chat router."""
    app = FastAPI(title="Chat API Smoke Test")
    store = ControlPlaneStore()
    app.state.store = store
    app.include_router(chat_router)
    return app, store


# ---------------------------------------------------------------------------
# Test routines
# ---------------------------------------------------------------------------

async def _run_tests(base_url: str, store: ControlPlaneStore) -> None:
    """Execute a sequence of HTTP calls against the running server."""
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:

        # 1. Health-check (just confirm the server is up)
        print(">>> GET /api/chat/sessions")
        resp = await client.get("/api/chat/sessions")
        print(f"    status={resp.status_code}  body={resp.json()}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

        # 2. List sessions filtered by a non-existent agent
        print(">>> GET /api/chat/sessions?agent_id=nonexistent")
        resp = await client.get("/api/chat/sessions", params={"agent_id": "nonexistent"})
        print(f"    status={resp.status_code}  body={resp.json()}")
        assert resp.status_code == 200

        # 3. History for a non-existent session → 404
        print(">>> GET /api/chat/history?session_id=does-not-exist")
        resp = await client.get("/api/chat/history", params={"session_id": "does-not-exist"})
        print(f"    status={resp.status_code}  detail={resp.json().get('detail', '')}")
        assert resp.status_code == 404

        # 4. Delete a non-existent session → 404
        print(">>> DELETE /api/chat/sessions/does-not-exist")
        resp = await client.delete("/api/chat/sessions/does-not-exist")
        print(f"    status={resp.status_code}  detail={resp.json().get('detail', '')}")
        assert resp.status_code == 404

        # 5. Chat with a non-existent agent → 404
        print(">>> POST /api/chat  (agent_id=nonexistent)")
        resp = await client.post("/api/chat", json={
            "agent_id": "nonexistent",
            "message": "Hello",
        })
        print(f"    status={resp.status_code}  detail={resp.json().get('detail', '')}")
        assert resp.status_code == 404

        # 6. Chat with a real agent (if any created agent exists)
        agent_id = _find_any_agent_id(store)
        if agent_id:
            print(f">>> POST /api/chat  (agent_id={agent_id})")
            resp = await client.post("/api/chat", json={
                "agent_id": agent_id,
                "message": "Hi, this is a smoke test. Reply briefly.",
            })
            print(f"    status={resp.status_code}")
            if resp.status_code == 200:
                body = resp.json()
                session_id = body["session_id"]
                print(f"    session_id={session_id}")
                print(f"    content={body['content'][:200]}")

                # 7. Verify history was persisted
                print(f">>> GET /api/chat/history?session_id={session_id}")
                resp2 = await client.get("/api/chat/history", params={"session_id": session_id})
                print(f"    status={resp2.status_code}  messages={len(resp2.json())}")
                assert resp2.status_code == 200
                assert len(resp2.json()) >= 2  # user + assistant

                # 8. Verify session shows up in list
                print(f">>> GET /api/chat/sessions?agent_id={agent_id}")
                resp3 = await client.get("/api/chat/sessions", params={"agent_id": agent_id})
                print(f"    status={resp3.status_code}  sessions={len(resp3.json())}")
                assert resp3.status_code == 200

                # 9. Delete the test session
                print(f">>> DELETE /api/chat/sessions/{session_id}")
                resp4 = await client.delete(f"/api/chat/sessions/{session_id}")
                print(f"    status={resp4.status_code}")
                assert resp4.status_code == 200

                print("\n=== All tests passed ===")
            else:
                print(f"    Chat failed (may be expected if model is not configured): "
                      f"{resp.json().get('detail', '')}")
                print("\n=== Non-chat tests passed; chat skipped ===")
        else:
            print("\n    No created agents found — chat test skipped.")
            print("=== Non-chat tests passed; chat skipped ===")


def _find_any_agent_id(store: ControlPlaneStore) -> str | None:
    """Return the first known agent_id from persisted agent state."""
    for agent_id in list(store.agent_states.keys()):
        return agent_id
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    app, store = _create_test_app()

    config = uvicorn.Config(app, host="127.0.0.1", port=18765, log_level="warning")
    server = uvicorn.Server(config)

    # Start server in background.
    server_task = asyncio.create_task(server.serve())

    # Wait for the server to be ready.
    for _ in range(40):
        await asyncio.sleep(0.25)
        if server.started:
            break

    try:
        await _run_tests("http://127.0.0.1:18765", store)
    except Exception as exc:
        print(f"\n!!! Test failed: {exc}")
        sys.exit(1)
    finally:
        server.should_exit = True
        await server_task


if __name__ == "__main__":
    asyncio.run(main())
