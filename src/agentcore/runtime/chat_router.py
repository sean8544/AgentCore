"""Chat API router: synchronous chat endpoints with session management.

Provides a simple synchronous chat endpoint that invokes agents via the
deepagents SDK.  Agent configuration is resolved from the workspace's
``agent.json`` file and agent graphs are built through the shared
:class:`~agentcore.runtime.agent_factory.AgentFactory` (the single
config→``create_deep_agent`` mapping used by every runtime path).
Created agent graphs are cached in-memory for reuse.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field

from agentcore.runtime import paths
from agentcore.runtime.workspace import BOOTSTRAP_MD_NAME

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-application chat state (checkpointer + agent graph cache)
#
# All mutable chat runtime state lives in a :class:`ChatState` instance
# mounted on ``app.state.chat_state`` by the application lifespan — NOT in
# module-level globals.  This keeps one FastAPI app instance self-contained
# (required for lifespan-based hosting) and makes the state easy to reset.
#
# :data:`_default_chat_state` is a fallback used only when the app runs
# without lifespan (e.g. synchronous test clients or direct unit calls).
# ---------------------------------------------------------------------------


@dataclass
class ChatState:
    """Mutable chat runtime state owned by a single FastAPI application."""

    checkpointer: AsyncSqliteSaver | None = None
    # Compiled agent graphs keyed by agent_id — rebuilt on invalidation.
    graph_cache: dict[str, Any] = field(default_factory=dict)
    # LangGraph thread_ids (= session_ids) used per agent so graph
    # invalidation can also purge checkpointed state.  The SDK's
    # memory/skills middleware load only once per thread (contents are
    # cached in checkpoint state), so stale threads would otherwise keep
    # outdated kernel files / skill lists forever after a reload.
    threads: dict[str, set[str]] = field(default_factory=dict)


_default_chat_state = ChatState()


def get_chat_state(state: Any) -> ChatState:
    """Resolve the :class:`ChatState` from ``app.state``.

    Accepts a Starlette state object carrying a ``chat_state`` attribute
    (populated during lifespan) and falls back to the process-wide default
    when absent — keeping the app usable without lifespan.
    """
    chat_state = getattr(state, "chat_state", None)
    if chat_state is None:
        chat_state = _default_chat_state
    return chat_state


def get_checkpointer(chat_state: ChatState) -> AsyncSqliteSaver:
    """Return the application-scoped :class:`AsyncSqliteSaver`.

    The saver keeps a long-lived connection to
    :func:`agentcore.runtime.paths.get_checkpoints_path` so that
    conversation state persists across requests (and server restarts)
    keyed by LangGraph ``thread_id`` — which we map 1:1 to the chat
    ``session_id``.

    Note: :meth:`AsyncSqliteSaver.from_conn_string` is an async context
    manager, so we build the saver directly from a connection instead, which
    is equivalent for an application-lifetime object.
    """
    if chat_state.checkpointer is None:
        checkpoint_db = paths.get_checkpoints_path()
        checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
        conn = aiosqlite.connect(str(checkpoint_db))
        chat_state.checkpointer = AsyncSqliteSaver(conn)
        logger.info("LangGraph checkpointer initialised at %s", checkpoint_db)
    return chat_state.checkpointer


async def close_checkpointer(chat_state: ChatState) -> None:
    """Close the checkpointer's SQLite connection (called on app shutdown).

    Without this the background ``aiosqlite`` connection thread keeps the
    interpreter alive at exit.
    """
    if chat_state.checkpointer is not None:
        try:
            await chat_state.checkpointer.conn.close()
        except Exception:
            logger.exception("Failed to close checkpointer connection")
        finally:
            chat_state.checkpointer = None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


def _get_store(request: Request):
    """Extract :class:`ControlPlaneStore` from ``request.app.state``."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="ControlPlaneStore is not available.",
        )
    return store


# ---------------------------------------------------------------------------
# Agent graph cache (lives in app.state — see ChatState above)
# ---------------------------------------------------------------------------


async def invalidate_agent_graph(
    state: Any, agent_id: str | None = None
) -> None:
    """Drop cached agent graphs so they are rebuilt with fresh config.

    *state* is the owning application's ``app.state`` (the chat state is
    resolved through :func:`get_chat_state`).  Pass an *agent_id* to
    invalidate a single agent, or ``None`` to clear the entire cache.
    Called after workspace reload / kernel-file edits so the next chat
    recreates the agent with the new configuration.

    In addition the checkpoint state of every tracked session belonging to
    the invalidated agent(s) is deleted — the SDK's ``MemoryMiddleware`` /
    ``SkillsMiddleware`` skip loading when ``memory_contents`` /
    ``skills_metadata`` already exist in checkpointed state, so without
    this purge kernel-file or skill changes would never reach existing
    conversations.
    """
    chat_state = get_chat_state(state)
    if agent_id is None:
        targets = sorted(set(chat_state.graph_cache) | set(chat_state.threads))
        chat_state.graph_cache.clear()
        logger.info("Agent graph cache fully invalidated")
    else:
        targets = [agent_id]
        if chat_state.graph_cache.pop(agent_id, None) is not None:
            logger.info("Agent graph cache invalidated for %s", agent_id)

    # Purge checkpoint state of the affected sessions so memory/skills are
    # re-read from disk on the next invoke.
    thread_ids: set[str] = set()
    for aid in targets:
        thread_ids |= chat_state.threads.pop(aid, set())
    if thread_ids and chat_state.checkpointer is not None:
        for thread_id in thread_ids:
            try:
                await chat_state.checkpointer.adelete_thread(thread_id)
            except Exception:
                logger.exception(
                    "Failed to delete checkpoint state for thread %s", thread_id
                )
        logger.info("Checkpoint state purged for %d session(s)", len(thread_ids))


# ---------------------------------------------------------------------------
# Agent graph resolution
# ---------------------------------------------------------------------------


def _resolve_agent_graph(
    agent_id: str,
    workspace: Any,
    factory: Any = None,
    manager: Any = None,
    chat_state: ChatState | None = None,
) -> Any:
    """Resolve *agent_id* to a compiled agent graph.

    Lookup strategy:

    1. Check the in-memory cache.
    2. Read the agent configuration from the workspace's ``agent.json``.
    3. When ``settings["enable_subagents"]`` is enabled, aggregate the
       other loaded agents (via
       :class:`~agentcore.runtime.subagent_registry.SubAgentRegistry`)
       as deepagents ``SubAgent`` specs so this agent can delegate to
       them through the SDK's ``task`` tool.
    4. Delegate graph construction to the shared
       :class:`~agentcore.runtime.agent_factory.AgentFactory`, which
       applies the full configuration mapping — model, workspace-bound
       backend, permissions, HITL ``interrupt_on`` and tool exclusion —
       plus the workspace kernel files (``memory=[]``) and ``skills/``
       directory (``skills=[]``).
    5. Cache the resulting graph.
    """
    if chat_state is None:
        chat_state = _default_chat_state
    if agent_id in chat_state.graph_cache:
        return chat_state.graph_cache[agent_id]

    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Workspace for agent {agent_id!r} is not available. "
                "Create the agent first (POST /api/agents)."
            ),
        )

    # Build the agent via the shared AgentFactory — the single
    # agent.json→create_deep_agent mapping.  The factory binds the
    # backend to the workspace directory, injects kernel files via
    # ``memory=[]``, exposes ``skills/``, and applies permissions /
    # HITL / tool exclusion from the configuration's settings.
    if factory is None:
        from agentcore.runtime.agent_factory import AgentFactory

        factory = AgentFactory()

    agent_config = workspace.read_agent_config()
    workspace_dir = workspace.workspace_dir

    # --- Opt-in subagents: inject the other loaded agents --------------
    # Disabled by default so agents stay mutually invisible unless an
    # agent explicitly sets ``settings.enable_subagents: true``.
    subagents: list[Any] | None = None
    settings = agent_config.get("settings", {}) or {}
    if settings.get("enable_subagents") and manager is not None:
        from agentcore.runtime.subagent_registry import SubAgentRegistry

        subagents = SubAgentRegistry(manager).get_subagents(
            exclude_agent_id=agent_id
        )
        logger.info(
            "Agent %s: injecting %d subagent(s): %s",
            agent_id,
            len(subagents),
            [spec["name"] for spec in subagents],
        )

    # LangGraph checkpointer — enables multi-turn conversation memory:
    # each thread_id (= session_id) keeps its full message history so the
    # model sees prior turns during inference.
    logger.info("Creating agent %s via AgentFactory", agent_id)
    try:
        agent_graph = factory.create_agent(
            agent_config,
            workspace_dir=workspace_dir,
            workspace=workspace,
            checkpointer=get_checkpointer(chat_state),
            subagents=subagents,
        )
    except Exception as exc:
        logger.exception("Failed to create agent %s", agent_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create agent {agent_id!r}: {exc}",
        ) from exc

    chat_state.graph_cache[agent_id] = agent_graph
    return agent_graph


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Incoming chat message."""

    agent_id: str
    message: str
    session_id: str | None = None  # auto-created when omitted


class ChatResponse(BaseModel):
    """Complete assistant reply."""

    session_id: str
    agent_id: str
    content: str
    timestamp: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class SessionInfo(BaseModel):
    """Lightweight session descriptor returned by list endpoint."""

    session_id: str
    agent_id: str
    created_at: str
    updated_at: str
    message_count: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _ensure_session(
    store: Any,
    session_id: str | None,
    agent_id: str,
) -> str:
    """Return a valid session id, creating one if *session_id* is ``None``."""
    if session_id is not None:
        existing = store.load_session(session_id)
        if existing is not None:
            return session_id
        store.save_session(session_id, {
            "session_id": session_id,
            "agent_id": agent_id,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "messages": [],
        })
        return session_id

    new_id = uuid.uuid4().hex
    store.save_session(new_id, {
        "session_id": new_id,
        "agent_id": agent_id,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "messages": [],
    })
    return new_id


def _append_message(
    store: Any,
    session_id: str,
    role: str,
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    """Append a user/assistant message to the session and persist."""
    session = store.load_session(session_id)
    if session is None:
        return
    messages: list[dict] = session.get("messages", [])
    msg: dict[str, Any] = {
        "role": role,
        "content": content,
        "timestamp": _now_iso(),
    }
    if tool_calls:
        msg["tool_calls"] = tool_calls
    messages.append(msg)
    session["messages"] = messages
    session["updated_at"] = _now_iso()
    store.save_session(session_id, session)


def _extract_usage_from_result(result: Any) -> tuple[int, int]:
    """Sum ``(input_tokens, output_tokens)`` from AI messages' usage metadata.

    LangChain ``AIMessage`` objects carry ``usage_metadata``
    (``{"input_tokens": ..., "output_tokens": ..., "total_tokens": ...}``)
    when the provider reports token counts.  With a checkpointer attached
    the result contains the full thread history, so only messages after the
    last human message (this turn) are counted.
    """
    if isinstance(result, dict):
        messages = result.get("messages", [])
    elif isinstance(result, list):
        messages = result
    else:
        return 0, 0

    last_human = -1
    for idx, msg in enumerate(messages):
        msg_type = getattr(msg, "type", None)
        if msg_type is None and isinstance(msg, dict):
            msg_type = msg.get("type")
        if msg_type == "human":
            last_human = idx
    if last_human >= 0:
        messages = messages[last_human + 1:]

    input_tokens = 0
    output_tokens = 0
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        if msg_type is None and isinstance(msg, dict):
            msg_type = msg.get("type")
        if msg_type is not None and msg_type != "ai":
            continue
        usage = getattr(msg, "usage_metadata", None)
        if usage is None and isinstance(msg, dict):
            usage = msg.get("usage_metadata")
        if not usage:
            continue
        input_tokens += int(usage.get("input_tokens", 0) or 0)
        output_tokens += int(usage.get("output_tokens", 0) or 0)
    return input_tokens, output_tokens


def _extract_text_from_result(result: Any) -> tuple[str, list[dict[str, Any]]]:
    """Extract text content and tool calls from agent output.

    The deepagents SDK (LangGraph) returns ``{"messages": [...]}``.  Only
    ``AI`` messages contribute reply text — human echoes and tool results
    are skipped.  The last message is typically the final ``AIMessage``
    with ``.content`` and optionally ``.tool_calls``.

    With a checkpointer attached, the returned state contains the **full
    thread history**, so only messages produced during the current
    invocation (i.e. after the last human message) belong to this turn's
    reply.
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    if isinstance(result, dict):
        messages = result.get("messages", [])
    elif isinstance(result, list):
        messages = result
    else:
        return str(result), []

    # Narrow to the current turn: drop everything up to and including the
    # last human message (the request we just sent).
    last_human = -1
    for idx, msg in enumerate(messages):
        msg_type = getattr(msg, "type", None)
        if msg_type is None and isinstance(msg, dict):
            msg_type = msg.get("type")
        if msg_type == "human":
            last_human = idx
    if last_human >= 0:
        messages = messages[last_human + 1:]

    for msg in messages:
        # Skip non-AI messages (human input, tool results, system prompts).
        msg_type = getattr(msg, "type", None)
        if msg_type is None and isinstance(msg, dict):
            msg_type = msg.get("type")
        if msg_type is not None and msg_type != "ai":
            continue

        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content", "")
        if isinstance(content, str) and content:
            text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

        tc = getattr(msg, "tool_calls", None)
        if tc is None and isinstance(msg, dict):
            tc = msg.get("tool_calls")
        if tc:
            for call in tc:
                if isinstance(call, dict):
                    tool_calls.append({
                        "name": call.get("name", ""),
                        "args": call.get("args", {}),
                        "id": call.get("id", ""),
                    })

    return "\n".join(text_parts), tool_calls


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest, req: Request) -> ChatResponse:
    """Synchronous chat — send a message, wait for the full response.

    The agent is created via the shared
    :class:`~agentcore.runtime.agent_factory.AgentFactory` using the
    ``agent.json`` configuration stored in the agent's workspace.
    """
    store = _get_store(req)

    session_id = _ensure_session(store, request.session_id, request.agent_id)

    # Persist user message.
    _append_message(store, session_id, "user", request.message)

    # Resolve the agent's workspace so the agent.json config and kernel
    # files (agent.md / profile.md / soul.md) are available.
    workspace = None
    manager = getattr(req.app.state, "agent_manager", None)
    if manager is not None:
        try:
            workspace = await manager.get_or_create_workspace(request.agent_id)
        except Exception:
            logger.exception(
                "Failed to load workspace for agent %s",
                request.agent_id,
            )

    # Resolve agent graph from the workspace config (cached, factory-built).
    # The graph cache lives on the app-scoped ChatState (app.state).
    factory = getattr(req.app.state, "factory", None)
    chat_state = get_chat_state(req.app.state)
    agent_graph = _resolve_agent_graph(
        request.agent_id,
        workspace,
        factory=factory,
        manager=manager,
        chat_state=chat_state,
    )

    # Track the session thread so invalidation can purge checkpointed
    # memory/skills state (they are loaded once per thread by the SDK).
    chat_state.threads.setdefault(request.agent_id, set()).add(session_id)

    # Bootstrap closed loop: remember whether bootstrap.md exists before
    # the invocation.  If the agent deletes it during this turn (via the
    # delete tool, as bootstrap.md instructs), the graph cache is
    # invalidated afterwards so the next chat rebuilds the agent without
    # the first-run guidance in its system_prompt.
    bootstrap_path = (
        workspace.workspace_dir / BOOTSTRAP_MD_NAME if workspace is not None else None
    )
    bootstrap_existed_before = bool(bootstrap_path and bootstrap_path.exists())

    # Invoke the agent.  The session_id doubles as the LangGraph thread_id
    # so the checkpointer replays prior conversation turns into the model.
    try:
        input_data = {"messages": [HumanMessage(content=request.message)]}
        result = await agent_graph.ainvoke(
            input_data,
            config={"configurable": {"thread_id": session_id}},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Agent %s invocation failed", request.agent_id)
        raise HTTPException(
            status_code=500,
            detail=f"Agent invocation failed: {exc}",
        ) from exc

    content, tool_calls = _extract_text_from_result(result)

    # Bootstrap closed loop (second half): bootstrap.md disappeared during
    # this turn ⇒ initial setup finished ⇒ drop the cached graph (which
    # still carries the guidance in its system_prompt / checkpoint state).
    # Best-effort only — never break the chat flow.
    if bootstrap_existed_before and not bootstrap_path.exists():
        try:
            await invalidate_agent_graph(req.app.state, request.agent_id)
            logger.info(
                "Agent %s: bootstrap.md removed during chat — agent graph "
                "invalidated",
                request.agent_id,
            )
        except Exception:
            logger.exception(
                "Agent %s: failed to invalidate graph after bootstrap "
                "removal",
                request.agent_id,
            )

    # Record token consumption (best-effort; never breaks the chat flow).
    input_tokens, output_tokens = _extract_usage_from_result(result)
    if input_tokens or output_tokens:
        from agentcore.runtime.token_router import record_token_usage

        record_token_usage(request.agent_id, input_tokens, output_tokens)

    # Persist assistant reply.
    _append_message(store, session_id, "assistant", content, tool_calls or None)

    return ChatResponse(
        session_id=session_id,
        agent_id=request.agent_id,
        content=content,
        timestamp=_now_iso(),
        tool_calls=tool_calls,
    )


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(
    req: Request,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """List all sessions, optionally filtered by *agent_id*."""
    store = _get_store(req)
    all_sessions = store.load_sessions()

    results: list[dict[str, Any]] = []
    for sid, data in all_sessions.items():
        if agent_id is not None and data.get("agent_id") != agent_id:
            continue
        results.append({
            "session_id": data.get("session_id", sid),
            "agent_id": data.get("agent_id", ""),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "message_count": len(data.get("messages", [])),
        })

    results.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return results


@router.get("/history")
async def chat_history(
    req: Request,
    session_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Retrieve message history for a session."""
    store = _get_store(req)
    session = store.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")

    messages: list[dict] = session.get("messages", [])
    recent = messages[-limit:] if len(messages) > limit else messages
    return recent


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, req: Request) -> dict[str, str]:
    """Delete a session and its message history."""
    store = _get_store(req)
    session = store.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")

    store.delete_session(session_id)

    # Drop the conversation's checkpoint state so a future session with
    # the same id starts clean, and stop tracking it for purges.
    chat_state = get_chat_state(req.app.state)
    if chat_state.checkpointer is not None:
        try:
            await chat_state.checkpointer.adelete_thread(session_id)
        except Exception:
            logger.exception(
                "Failed to delete checkpoint state for session %s", session_id
            )
    for threads in chat_state.threads.values():
        threads.discard(session_id)

    return {"result": "ok", "session_id": session_id}
