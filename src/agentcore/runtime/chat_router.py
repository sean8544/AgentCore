"""Chat API router: synchronous chat endpoints with session management.

Provides a simple synchronous chat endpoint that invokes agents via the
deepagents SDK.  Agent configuration is resolved from the workspace's
``agent.json`` file and agent graphs are built through the shared
:class:`~agentcore.runtime.agent_factory.AgentFactory` (the single
config→``create_deep_agent`` mapping used by every runtime path).
Created agent graphs are cached in-memory for reuse.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Literal

import aiosqlite
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from agentcore.runtime import paths
from agentcore.runtime.workspace import BOOTSTRAP_MD_NAME

# Backwards-compatible key used by LangGraph v1 ainvoke() to surface
# interrupts in the result dict.  Value: list[Interrupt].
_INTERRUPT_KEY = "__interrupt__"

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

    # Global security settings (danger-operation approval) apply to every
    # agent: merge them into the config before building the graph so the
    # factory maps the combined ``interrupt_rules`` onto ``interrupt_on``.
    from agentcore.runtime.security_router import apply_global_approval

    merged_settings = apply_global_approval(settings)
    if merged_settings is not settings:
        agent_config = {**agent_config, "settings": merged_settings}
        settings = merged_settings

    if settings.get("enable_subagents") and manager is not None:
        from agentcore.runtime.subagent_registry import SubAgentRegistry

        # Pass subagent_ids whitelist if configured (None = all agents)
        subagent_whitelist = settings.get("subagent_ids") or None
        subagents = SubAgentRegistry(manager).get_subagents(
            exclude_agent_id=agent_id,
            whitelist=subagent_whitelist,
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
    status: str = "complete"
    approval_request: dict[str, Any] | None = None


class ApprovalRequest(BaseModel):
    """Incoming approval decision for a HITL interrupt."""

    decision: Literal["approve", "edit", "reject", "respond"]
    edited_args: dict[str, Any] | None = None
    operator: str | None = None
    message: str | None = None  # optional rejection message


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


def _extract_interrupts_from_result(result: Any) -> list[Any]:
    """Extract ``Interrupt`` objects from an agent result dict.

    LangGraph v1 ``ainvoke()`` includes ``__interrupt__`` in the result
    dict when the graph paused due to an interrupt (e.g. HITL approval).
    """
    if not isinstance(result, dict):
        return []
    interrupts = result.get(_INTERRUPT_KEY)
    if interrupts is None:
        return []
    return list(interrupts) if isinstance(interrupts, (list, tuple)) else []


def _build_approval_request_info(interrupts: list[Any]) -> dict[str, Any]:
    """Build a serialisable approval-request payload from interrupts.

    The SDK's ``HumanInTheLoopMiddleware`` passes a ``HITLRequest`` dict
    as the interrupt value, containing ``action_requests`` and
    ``review_configs``.  Each action carries its ``allowed_decisions``
    (``None`` when the policy is absent — the client then renders the
    full default button set).
    """
    actions: list[dict[str, Any]] = []
    for interrupt_obj in interrupts:
        value = getattr(interrupt_obj, "value", None)
        if value is None and isinstance(interrupt_obj, dict):
            value = interrupt_obj.get("value")
        if not isinstance(value, dict):
            continue
        action_requests = value.get("action_requests", [])
        review_configs = value.get("review_configs", [])
        rc_by_name = {
            rc.get("action_name"): rc
            for rc in review_configs
            if isinstance(rc, dict) and rc.get("action_name")
        }
        for ar in action_requests:
            rc = rc_by_name.get(ar.get("name"), {})
            actions.append({
                "name": ar.get("name", ""),
                "args": ar.get("args", {}),
                "description": ar.get("description", ""),
                "allowed_decisions": rc.get("allowed_decisions") or None,
            })
    return {"actions": actions}


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
    extras: dict[str, Any] | None = None,
) -> None:
    """Append a user/assistant message to the session and persist.

    *extras* are merged into the stored message verbatim (e.g. ``reasoning``,
    ``todos``, ``delegations``, ``approval_request``) so session history is a
    faithful record of the turn.
    """
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
    if extras:
        msg.update(extras)
    messages.append(msg)
    session["messages"] = messages
    session["updated_at"] = _now_iso()
    store.save_session(session_id, session)


def _extract_message_reasoning(msg: Any) -> str:
    """Best-effort extraction of reasoning/deliberation text from an AI message.

    Reasoning models (e.g. Qwen3 through ChatOpenAI) surface their chain of
    thought either as an attribute (``reasoning_content``) or inside
    ``additional_kwargs`` — probe both, plus generic ``reasoning`` /
    ``deliberation`` aliases.
    """
    for attr in ("reasoning_content", "reasoning", "deliberation"):
        val = getattr(msg, attr, None)
        if isinstance(val, str) and val:
            return val
    kwargs = getattr(msg, "additional_kwargs", None)
    if isinstance(kwargs, dict):
        for key in ("reasoning_content", "reasoning", "deliberation"):
            val = kwargs.get(key)
            if isinstance(val, str) and val:
                return val
    return ""


def _extract_chunk_reasoning(msg_chunk: Any) -> str:
    """Reasoning extraction for a streaming token chunk (AIMessageChunk)."""
    return _extract_message_reasoning(msg_chunk)


def _extract_chunk_usage(msg_chunk: Any, metadata: Any = None) -> dict[str, int] | None:
    """Extract cumulative token usage from a streaming chunk.

    In LangGraph's ``messages`` stream mode the usage is carried on the
    ``AIMessageChunk`` itself — either via the standard ``usage_metadata``
    attribute (LangChain convention) or in provider-specific spots such as
    ``response_metadata['token_usage']`` / ``additional_kwargs['token_usage']``
    (OpenAI-compatible APIs like DeepSeek). The stream metadata dict is only
    used as a last-resort fallback.
    """
    candidates: list[Any] = []
    for attr in ("usage_metadata", "usage"):
        val = getattr(msg_chunk, attr, None)
        if isinstance(val, dict):
            candidates.append(val)
    resp_meta = getattr(msg_chunk, "response_metadata", None)
    if isinstance(resp_meta, dict) and isinstance(resp_meta.get("token_usage"), dict):
        candidates.append(resp_meta["token_usage"])
    add_kwargs = getattr(msg_chunk, "additional_kwargs", None)
    if isinstance(add_kwargs, dict) and isinstance(add_kwargs.get("token_usage"), dict):
        candidates.append(add_kwargs["token_usage"])
    if isinstance(metadata, dict):
        for key in ("usage", "usage_metadata"):
            val = metadata.get(key)
            if isinstance(val, dict):
                candidates.append(val)

    for usage in candidates:
        inp = usage.get("input_tokens") or usage.get("prompt_tokens")
        out = usage.get("output_tokens") or usage.get("completion_tokens")
        try:
            inp_i, out_i = int(inp or 0), int(out or 0)
        except (TypeError, ValueError):
            continue
        if inp_i or out_i:
            return {"input_tokens": inp_i, "output_tokens": out_i}
    return None


def _collect_turn_metadata(result: Any) -> dict[str, Any]:
    """Extract reasoning, todo plan and delegations from an invocation result.

    Mirrors what the streaming endpoint persists per turn, so synchronous
    chat history carries the same metadata fields.
    """
    if isinstance(result, dict):
        messages = result.get("messages", [])
    elif isinstance(result, list):
        messages = result
    else:
        return {}

    # Narrow to the current turn (same logic as _extract_text_from_result).
    last_human = -1
    for idx, msg in enumerate(messages):
        msg_type = getattr(msg, "type", None)
        if msg_type is None and isinstance(msg, dict):
            msg_type = msg.get("type")
        if msg_type == "human":
            last_human = idx
    if last_human >= 0:
        messages = messages[last_human + 1:]

    reasoning_parts: list[str] = []
    latest_todos: list[dict[str, Any]] | None = None
    delegations: list[dict[str, Any]] = []

    for msg in messages:
        msg_type = getattr(msg, "type", None)
        if msg_type is None and isinstance(msg, dict):
            msg_type = msg.get("type")
        if msg_type is not None and msg_type != "ai":
            continue

        reasoning = _extract_message_reasoning(msg)
        if reasoning:
            reasoning_parts.append(reasoning)

        tc = getattr(msg, "tool_calls", None)
        if tc is None and isinstance(msg, dict):
            tc = msg.get("tool_calls")
        if not tc:
            continue
        for call in tc:
            if not isinstance(call, dict):
                continue
            name = call.get("name", "")
            args = call.get("args", {}) or {}
            if name == "write_todos" and args.get("todos"):
                latest_todos = args["todos"]
            elif name == "task" and args.get("subagent_type"):
                delegations.append({
                    "subagent": args["subagent_type"],
                    "description": args.get("description", ""),
                })

    meta: dict[str, Any] = {}
    if reasoning_parts:
        meta["reasoning"] = "\n".join(reasoning_parts)
    if latest_todos:
        meta["todos"] = latest_todos
    if delegations:
        meta["delegations"] = delegations
    return meta


def _mark_last_pending_approval(
    store: Any,
    session_id: str,
    decision: str,
    tool_name: str,
    new_approval_request: dict[str, Any] | None = None,
    clear_request: bool = False,
) -> None:
    """Record an approval decision on the assistant message that requested it.

    The last assistant message carrying an ``approval_request`` gets an
    ``approval`` field describing the decision; when a further interrupt fires
    (*new_approval_request*), the pending request is refreshed in place.  When
    the approval chain finishes (*clear_request*), the stale pending request
    is removed so history does not keep showing an actionable card.
    """
    session = store.load_session(session_id)
    if session is None:
        return
    messages: list[dict] = session.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("approval_request"):
            # Fall back to the persisted request's own action name — the
            # graph-state probe can come back empty after a resume.
            resolved_tool = tool_name
            if not resolved_tool:
                actions = (msg.get("approval_request") or {}).get("actions", [])
                if actions:
                    resolved_tool = actions[0].get("name", "")
            msg["approval"] = {
                "decision": decision,
                "tool_name": resolved_tool,
                "timestamp": _now_iso(),
            }
            if new_approval_request is not None:
                msg["approval_request"] = new_approval_request
            elif clear_request:
                msg.pop("approval_request", None)
            break
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
    # files (agent.md / memory files) are available.
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

    # --- HITL interrupt detection (Task 1.2) --------------------------
    # If the graph paused due to a HITL interrupt, surface the approval
    # request instead of a normal reply.
    interrupts = _extract_interrupts_from_result(result)
    if interrupts:
        approval_info = _build_approval_request_info(interrupts)
        logger.info(
            "Agent %s: HITL interrupt detected in session %s — %d action(s) "
            "pending approval",
            request.agent_id,
            session_id,
            len(approval_info.get("actions", [])),
        )
        # Persist the pending approval so session history shows the request.
        _append_message(
            store, session_id, "assistant", "",
            extras={"approval_request": approval_info},
        )
        return ChatResponse(
            session_id=session_id,
            agent_id=request.agent_id,
            content="",
            timestamp=_now_iso(),
            status="pending_approval",
            approval_request=approval_info,
        )

    content, tool_calls = _extract_text_from_result(result)
    content = _normalize_assistant_text(content)

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

    # Persist assistant reply with the turn's metadata (reasoning, todo
    # plan, delegations) so history is complete for sync chat too.
    extras = _collect_turn_metadata(result)
    _append_message(
        store, session_id, "assistant", content, tool_calls or None,
        extras=extras or None,
    )

    # Phase 3: Archive this turn's messages to the daily memory archive.
    # Best-effort only — never breaks the chat flow.
    if workspace is not None:
        try:
            from datetime import datetime as _dt
            date_str = _dt.now(tz=timezone.utc).strftime("%Y-%m-%d")
            workspace.archive_session_messages(
                date_str,
                request.agent_id,
                [
                    {"role": "user", "content": request.message, "timestamp": _now_iso()},
                    {"role": "assistant", "content": content, "timestamp": _now_iso()},
                ],
            )
        except Exception:
            logger.warning(
                "Failed to archive session messages for agent %s",
                request.agent_id,
                exc_info=True,
            )

    return ChatResponse(
        session_id=session_id,
        agent_id=request.agent_id,
        content=content,
        timestamp=_now_iso(),
        tool_calls=tool_calls,
    )


# ---------------------------------------------------------------------------
# Streaming SSE endpoint (Tasks 3.1, 3.2)
# ---------------------------------------------------------------------------


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a single SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _normalize_assistant_text(text: str) -> str:
    """Clean token-boundary noise from streamed assistant text.

    Streaming models (e.g. Qwen3) emit one chunk per token where every
    chunk ends with ``\n``, so naive concatenation produces replies where
    each word sits on its own line.  Collapse a single newline between
    two non-newline characters back into a space (real paragraphs use
    ``\n\n`` and survive), and clamp 3+ consecutive newlines to two.
    """
    text = re.sub(r"([^\n])\n(?=[^\n])", r"\1 ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _append_assistant_turn(
    store: Any,
    session_id: str,
    text_parts: list[str],
    reasoning_parts: list[str],
    tool_calls: list[dict[str, Any]],
    latest_todos: list[dict[str, Any]] | None,
    delegations: list[dict[str, Any]],
    latest_approval: dict[str, Any] | None,
) -> None:
    """Persist one streaming turn as a single assistant message.

    Bundles the reply text, reasoning chain, tool calls, todo plan,
    delegations and approval request into the session history.
    """
    extras: dict[str, Any] = {}
    if reasoning_parts:
        extras["reasoning"] = _normalize_assistant_text(
            "\n".join(reasoning_parts)
        )
    if latest_todos:
        extras["todos"] = latest_todos
    if delegations:
        extras["delegations"] = delegations
    if latest_approval:
        extras["approval_request"] = latest_approval
    _append_message(
        store, session_id, "assistant",
        _normalize_assistant_text("\n".join(text_parts)),
        tool_calls or None,
        extras=extras,
    )


def _friendly_stream_error(exc: BaseException) -> str:
    """Render a user-friendly error message for stream failures.

    Provider throttling (HTTP 429 / rate-limit) gets a readable hint;
    everything else keeps the original detail for debugging.
    """
    text = str(exc)
    if (
        getattr(exc, "status_code", None) == 429
        or "RateLimitError" in type(exc).__name__
        or "429" in text
    ):
        return (
            "Stream error: 模型服务配额已用尽（429），请稍后再试，"
            "或检查模型账户余额/额度配置。"
        )
    return f"Stream error: {text}"


async def _stream_chat_sse(
    agent_graph: Any,
    session_id: str,
    agent_id: str,
    message: str,
    chat_state: ChatState,
    store: Any = None,
) -> AsyncGenerator[str, None]:
    """Generate SSE events from an agent stream.

    Five projection channels are emitted:

    * **messages** — token-by-token LLM output (``stream_mode="messages"``)
    * **subagents** — node updates showing subagent delegation
      (``stream_mode="updates"``)
    * **todo** — structured task plan updates from ``write_todos`` calls
    * **interrupts** — HITL interrupt events (``stream_mode="values"``)
    * **token_usage** — cumulative token counts extracted from message
      metadata

    *store* (when provided) is used to persist delegation records so a
    subagent's own workspace can later show who delegated what to it.
    """
    input_data = {"messages": [HumanMessage(content=message)]}
    config = {"configurable": {"thread_id": session_id}}

    # Track the session thread for invalidation purposes.
    chat_state.threads.setdefault(agent_id, set()).add(session_id)

    total_input = 0
    total_output = 0

    # Turn-level collectors — persisted as one assistant message at the end
    # so session history keeps the reply, reasoning, tool calls, todo plan,
    # delegations and approval requests of every turn.
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    latest_todos: list[dict[str, Any]] | None = None
    delegations: list[dict[str, Any]] = []
    latest_approval: dict[str, Any] | None = None
    # Track the index of the currently in-progress todo for real-time status updates.
    current_todo_idx: int = -1
    # When the agent manages todo statuses itself (write_todos calls carry
    # non-pending statuses), disable the heuristic auto-advance to avoid
    # conflicting updates.
    agent_managed_todos = False

    # Streaming tool-call accumulator — tool calls arrive as fragmented
    # chunks; we merge them by index and only emit an early ``tool_calls``
    # event once the arguments form valid JSON (the call is complete).
    pending_tool_chunks: dict[int, dict[str, Any]] = {}
    emitted_tool_call_keys: set[str] = set()

    def _process_tool_call_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge tool-call chunk deltas; return calls that became complete."""
        nonlocal latest_todos, current_todo_idx, agent_managed_todos
        completed: list[dict[str, Any]] = []
        for tc in chunks:
            if not isinstance(tc, dict):
                continue
            idx = tc.get("index") or 0
            acc = pending_tool_chunks.setdefault(
                idx, {"name": "", "args": "", "id": None}
            )
            if tc.get("name"):
                acc["name"] = tc["name"]
            if tc.get("id"):
                acc["id"] = tc["id"]
            acc["args"] += tc.get("args") or ""

            key = acc["id"] or f"{acc['name']}@{idx}"
            if key in emitted_tool_call_keys:
                continue
            try:
                parsed_args = json.loads(acc["args"]) if acc["args"] else None
            except (json.JSONDecodeError, ValueError):
                continue
            if parsed_args is None or not acc["name"]:
                continue
            emitted_tool_call_keys.add(key)
            completed.append({"name": acc["name"], "args": parsed_args})

            # write_todos is projected as a todo event, not a tool card.
            if acc["name"] == "write_todos":
                todos = parsed_args.get("todos", []) if isinstance(parsed_args, dict) else []
                if todos:
                    if any(t.get("status") not in (None, "pending") for t in todos):
                        agent_managed_todos = True
                    if not agent_managed_todos:
                        for todo in todos:
                            if todo.get("status") == "pending":
                                todo["status"] = "in_progress"
                                current_todo_idx = todos.index(todo)
                                break
                    latest_todos = todos
        return completed

    persisted = False

    def _advance_todo_status(completed_tool_name: str | None = None) -> list[dict[str, Any]] | None:
        """Advance todo status based on tool execution.

        When a tool completes, mark the current in-progress todo as completed
        and move to the next pending todo. Returns the updated todos list.
        """
        nonlocal latest_todos, current_todo_idx
        if agent_managed_todos or not latest_todos:
            return None

        # Mark current in-progress todo as completed.
        if 0 <= current_todo_idx < len(latest_todos):
            latest_todos[current_todo_idx]["status"] = "completed"

        # Find next pending todo and mark it as in_progress.
        next_idx = -1
        for i, todo in enumerate(latest_todos):
            if todo.get("status") == "pending":
                next_idx = i
                break

        if next_idx >= 0:
            latest_todos[next_idx]["status"] = "in_progress"
            current_todo_idx = next_idx
        else:
            current_todo_idx = -1

        return latest_todos

    def _persist_partial() -> None:
        """Persist the collected turn so far — idempotent.

        Called on the happy path (full turn), on stream errors and from
        ``finally`` when the client aborts / stops the conversation so
        the session history keeps whatever was already generated.
        """
        nonlocal persisted
        if persisted or store is None:
            return
        if not (text_parts or tool_calls or reasoning_parts
                or latest_todos or delegations or latest_approval):
            return
        try:
            _append_assistant_turn(
                store, session_id, text_parts, reasoning_parts,
                tool_calls, latest_todos, delegations, latest_approval,
            )
            persisted = True
        except Exception:
            logger.exception(
                "Failed to persist assistant message for session %s",
                session_id,
            )

    try:
        async for chunk in agent_graph.astream(
            input_data,
            config=config,
            stream_mode=["updates", "messages", "values"],
        ):
            # When multiple stream_modes are passed, each chunk is a
            # tuple of (mode, data).
            if not isinstance(chunk, (list, tuple)) or len(chunk) < 2:
                continue

            mode, data = chunk[0], chunk[1]

            if mode == "messages":
                # Token-by-token LLM output.
                # data is typically (message_chunk, metadata) tuple.
                if isinstance(data, (list, tuple)) and len(data) >= 1:
                    msg_chunk = data[0]
                    content = ""
                    if hasattr(msg_chunk, "content"):
                        content = msg_chunk.content or ""
                    elif isinstance(msg_chunk, dict):
                        content = msg_chunk.get("content", "")
                    if content:
                        text_parts.append(content)
                        yield _sse_event("messages", {
                            "content": content,
                            "agent_id": agent_id,
                        })

                    # Reasoning/deliberation tokens (Qwen3-style models).
                    reasoning = _extract_chunk_reasoning(msg_chunk)
                    if reasoning:
                        reasoning_parts.append(reasoning)
                        yield _sse_event("messages", {
                            "reasoning": reasoning,
                            "agent_id": agent_id,
                        })

                    # Early tool_calls detection — tool calls stream in as
                    # fragmented chunks; merge them and emit only when the
                    # arguments are complete (valid JSON) to avoid duplicate
                    # partial cards on the frontend.
                    raw_chunks = getattr(msg_chunk, "tool_call_chunks", None)
                    if raw_chunks is None and isinstance(msg_chunk, dict):
                        raw_chunks = msg_chunk.get("tool_call_chunks")
                    if raw_chunks:
                        completed_calls = _process_tool_call_chunks(list(raw_chunks))
                        # write_todos is projected as a todo event, not a card.
                        card_calls = [
                            c for c in completed_calls if c["name"] != "write_todos"
                        ]
                        if card_calls:
                            yield _sse_event("tool_calls", {
                                "tool_calls": card_calls,
                                "agent_id": agent_id,
                            })
                        for call in completed_calls:
                            if call["name"] == "write_todos":
                                args = call["args"]
                                todos_payload = (
                                    args.get("todos", [])
                                    if isinstance(args, dict) else []
                                )
                                if todos_payload:
                                    yield _sse_event("todo", {
                                        "todos": todos_payload,
                                        "agent_id": agent_id,
                                    })

                    # Extract token usage from the chunk itself (LangGraph
                    # carries usage on the AIMessageChunk, not the stream
                    # metadata) — fall back to the metadata dict.
                    usage = _extract_chunk_usage(
                        msg_chunk, data[1] if len(data) > 1 else None
                    )
                    if usage:
                        total_input += usage["input_tokens"]
                        total_output += usage["output_tokens"]
                        yield _sse_event("token_usage", {
                            "input_tokens": total_input,
                            "output_tokens": total_output,
                            "agent_id": agent_id,
                        })

            elif mode == "updates":
                # Node updates — includes subagent delegation events.
                if isinstance(data, dict):
                    for node_name, node_output in data.items():
                        # Detect subagent task delegation.
                        if node_name == "task" or "subagent" in node_name.lower():
                            yield _sse_event("subagents", {
                                "node": node_name,
                                "update": _serialise_node_output(node_output),
                                "agent_id": agent_id,
                            })
                        # Also surface tool execution updates.
                        if isinstance(node_output, dict):
                            msgs = node_output.get("messages", [])
                            for m in msgs:
                                tc = getattr(m, "tool_calls", None) or (
                                    m.get("tool_calls") if isinstance(m, dict) else None
                                )
                                if tc:
                                    calls = [
                                        {
                                            "name": c.get("name", "") if isinstance(c, dict) else getattr(c, "name", ""),
                                            "args": c.get("args", {}) if isinstance(c, dict) else getattr(c, "args", {}),
                                        }
                                        for c in tc
                                    ]
                                    # Keep every tool call of this turn for the
                                    # persisted assistant message.
                                    tool_calls.extend(calls)
                                    # Persist task delegation so the target
                                    # subagent can later see who delegated
                                    # what to it.
                                    for call in calls:
                                        if call.get("name") == "task":
                                            args = call.get("args", {}) or {}
                                            subagent_type = args.get("subagent_type", "")
                                            if subagent_type:
                                                delegations.append({
                                                    "subagent": subagent_type,
                                                    "description": args.get("description", ""),
                                                })
                                            if subagent_type and store is not None:
                                                try:
                                                    store.append_delegation(subagent_type, {
                                                        "parent_agent_id": agent_id,
                                                        "task_description": args.get("description", ""),
                                                        "args": args,
                                                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                                                    })
                                                except Exception:
                                                    logger.exception(
                                                        "Failed to persist delegation to %s",
                                                        subagent_type,
                                                    )
                                        elif call.get("name") == "write_todos":
                                            # Structured task plan update.
                                            todos = (call.get("args", {}) or {}).get("todos", [])
                                            if todos:
                                                if any(t.get("status") not in (None, "pending") for t in todos):
                                                    agent_managed_todos = True
                                                # Mark the first pending todo as in_progress
                                                # only when the agent does not manage statuses.
                                                if not agent_managed_todos:
                                                    for todo in todos:
                                                        if todo.get("status") == "pending":
                                                            todo["status"] = "in_progress"
                                                            current_todo_idx = todos.index(todo)
                                                            break
                                                latest_todos = todos
                                                yield _sse_event("todo", {
                                                    "todos": todos,
                                                    "agent_id": agent_id,
                                                })
                                    yield _sse_event("subagents", {
                                        "node": node_name,
                                        "tool_calls": calls,
                                        "agent_id": agent_id,
                                    })
                                    # Advance todo status based on tool execution.
                                    # write_todos itself does not advance progress.
                                    if any(c.get("name") != "write_todos" for c in calls):
                                        updated_todos = _advance_todo_status()
                                        if updated_todos:
                                            yield _sse_event("todo", {
                                                "todos": updated_todos,
                                                "agent_id": agent_id,
                                            })

            elif mode == "values":
                # Full state values — detect interrupts.
                if isinstance(data, dict):
                    interrupts = data.get(_INTERRUPT_KEY, [])
                    if interrupts:
                        approval_info = _build_approval_request_info(interrupts)
                        latest_approval = approval_info
                        yield _sse_event("interrupts", {
                            "status": "pending_approval",
                            "approval_request": approval_info,
                            "agent_id": agent_id,
                            "session_id": session_id,
                        })

    except Exception as exc:
        logger.exception("Streaming error for agent %s", agent_id)
        # Keep whatever was produced before the failure so the session
        # history does not silently lose the partial turn.
        _persist_partial()
        yield _sse_event("error", {
            "detail": _friendly_stream_error(exc),
            "agent_id": agent_id,
        })
        return
    finally:
        # Client abort / stop: the generator is closed here (CancelledError
        # is a BaseException, not caught above) — keep the partial turn.
        _persist_partial()

    # Persist the full assistant turn: reply text, reasoning, tool calls,
    # todo plan, delegations and any approval request.
    _persist_partial()

    # Emit final done event.
    yield _sse_event("done", {
        "agent_id": agent_id,
        "session_id": session_id,
        "input_tokens": total_input,
        "output_tokens": total_output,
    })


def _serialise_node_output(node_output: Any) -> dict[str, Any]:
    """Best-effort serialisation of a node update for SSE."""
    if isinstance(node_output, dict):
        result: dict[str, Any] = {}
        for k, v in node_output.items():
            if isinstance(v, (str, int, float, bool)):
                result[k] = v
            elif isinstance(v, list):
                result[k] = [_json_safe(item) for item in v]
            elif isinstance(v, dict):
                result[k] = {str(kk): str(vv) for kk, vv in v.items()}
            else:
                result[k] = str(v)
        return result
    return {"value": str(node_output)}


def _json_safe(value: Any) -> Any:
    """Recursively coerce a value into a JSON-serialisable shape."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


@router.post("/stream")
async def stream_chat(request: ChatRequest, req: Request) -> StreamingResponse:
    """Streaming chat — SSE output with 4 projection channels.

    Events:
    - ``messages``: token-by-token LLM output
    - ``subagents``: subagent delegation / tool execution updates
    - ``interrupts``: HITL approval requests
    - ``token_usage``: cumulative token consumption
    - ``done``: stream complete
    - ``error``: an error occurred
    """
    store = _get_store(req)
    session_id = _ensure_session(store, request.session_id, request.agent_id)
    _append_message(store, session_id, "user", request.message)

    workspace = None
    manager = getattr(req.app.state, "agent_manager", None)
    if manager is not None:
        try:
            workspace = await manager.get_or_create_workspace(request.agent_id)
        except Exception:
            logger.exception("Failed to load workspace for agent %s", request.agent_id)

    factory = getattr(req.app.state, "factory", None)
    chat_state = get_chat_state(req.app.state)
    agent_graph = _resolve_agent_graph(
        request.agent_id,
        workspace,
        factory=factory,
        manager=manager,
        chat_state=chat_state,
    )

    return StreamingResponse(
        _stream_chat_sse(
            agent_graph, session_id, request.agent_id,
            request.message, chat_state, store=store,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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


# ---------------------------------------------------------------------------
# HITL approval decision endpoint (Tasks 1.1, 1.3, 1.4)
# ---------------------------------------------------------------------------


@router.post("/{agent_id}/sessions/{session_id}/approval")
async def submit_approval(
    agent_id: str,
    session_id: str,
    approval: ApprovalRequest,
    req: Request,
) -> ChatResponse:
    """Submit a HITL approval decision for a paused session.

    Resumes the agent graph with the decision via the LangGraph
    checkpointer.  If the agent completes after resuming, the reply is
    returned directly.  If another interrupt fires, a new
    ``pending_approval`` response is returned.
    """
    store = _get_store(req)

    # Validate session exists.
    session = store.load_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id!r} not found",
        )

    # Resolve the cached agent graph.
    chat_state = get_chat_state(req.app.state)
    agent_graph = chat_state.graph_cache.get(agent_id)
    if agent_graph is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Agent graph for {agent_id!r} is not cached. "
                "The session may have expired."
            ),
        )

    if chat_state.checkpointer is None:
        raise HTTPException(
            status_code=503,
            detail="Checkpointer not available — cannot resume interrupted session.",
        )

    # --- Enforce the interrupt policy (allowed_decisions) -----------
    # The SDK's HumanInTheLoopMiddleware embeds each tool's allowed
    # decisions in the interrupt state.  Decisions outside that set are
    # rejected here — the UI only renders allowed buttons, but the API
    # is the actual enforcement boundary.
    if approval.decision == "respond":
        raise HTTPException(
            status_code=501,
            detail=(
                "The 'respond' decision is not supported yet — use "
                "approve / edit / reject."
            ),
        )
    allowed_decisions = await _get_interrupt_allowed_decisions(
        agent_graph, session_id
    )
    if allowed_decisions and approval.decision not in allowed_decisions:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Decision {approval.decision!r} is not allowed for this "
                f"interrupt (allowed: {sorted(allowed_decisions)})."
            ),
        )

    # --- Build the SDK decision objects for ALL pending tool calls -----
    # When multiple tools are called in a single turn, the SDK creates
    # multiple interrupts. We need to provide a decision for each one.
    all_pending_tools = await _get_all_interrupt_tools(agent_graph, session_id)
    if not all_pending_tools:
        # Fallback: no tool info available, use single decision.
        all_pending_tools = [{"name": "", "args": {}}]

    decisions: list[dict[str, Any]] = []
    for tool in all_pending_tools:
        decision: dict[str, Any]
        if approval.decision == "approve":
            decision = {"type": "approve"}
        elif approval.decision == "edit":
            if not approval.edited_args:
                raise HTTPException(
                    status_code=400,
                    detail="edited_args is required for edit decision",
                )
            # For edit, apply the edited args to the first tool only.
            # Other tools get approved with their original args.
            if len(decisions) == 0:
                decision = {
                    "type": "edit",
                    "edited_action": {
                        "name": tool.get("name", ""),
                        "args": approval.edited_args,
                    },
                }
            else:
                decision = {"type": "approve"}
        elif approval.decision == "reject":
            decision = {
                "type": "reject",
                "message": approval.message or "",
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown decision type: {approval.decision!r}",
            )
        decisions.append(decision)

    # --- Audit the decision (Task 1.4) --------------------------------
    # Audit the first tool for simplicity.
    tool_name_for_audit = all_pending_tools[0].get("name") or "unknown"
    final_args = (
        approval.edited_args
        if approval.decision == "edit" and approval.edited_args
        else all_pending_tools[0].get("args", {})
    )
    from agentcore.runtime.audit import record_approval_decision

    record_approval_decision(
        agent_id=agent_id,
        session_id=session_id,
        decision=approval.decision,
        tool_name=tool_name_for_audit,
        final_args=final_args,
        operator=approval.operator,
    )

    # --- Resume the graph with ALL decisions ---------------------------
    config = {"configurable": {"thread_id": session_id}}
    resume_value = {"decisions": decisions}

    try:
        result = await asyncio.wait_for(
            agent_graph.ainvoke(
                Command(resume=resume_value),
                config=config,
            ),
            timeout=180,
        )
    except asyncio.TimeoutError:
        logger.error(
            "Agent %s: approval resume timed out in session %s",
            agent_id,
            session_id,
        )
        raise HTTPException(
            status_code=504,
            detail=(
                "Agent did not respond within 180s after approval. "
                "The session may be busy; please try again later."
            ),
        ) from None
    except Exception as exc:
        logger.exception(
            "Agent %s: failed to resume after approval in session %s",
            agent_id,
            session_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resume agent: {exc}",
        ) from exc

    # Check for further interrupts (another approval round).
    more_interrupts = _extract_interrupts_from_result(result)
    if more_interrupts:
        approval_info = _build_approval_request_info(more_interrupts)
        # Record this decision and surface the next pending request on the
        # same persisted message.
        _mark_last_pending_approval(
            store, session_id, approval.decision,
            tool_name_for_audit or "",
            new_approval_request=approval_info,
        )
        return ChatResponse(
            session_id=session_id,
            agent_id=agent_id,
            content="",
            timestamp=_now_iso(),
            status="pending_approval",
            approval_request=approval_info,
        )

    # Agent completed — extract reply.
    content, tool_calls = _extract_text_from_result(result)
    content = _normalize_assistant_text(content)

    # Record the decision on the message that requested the approval, so
    # session history shows the full approve → resume → reply sequence, and
    # drop the stale pending request (the chain completed).
    _mark_last_pending_approval(
        store, session_id, approval.decision, tool_name_for_audit or "",
        clear_request=True,
    )

    _append_message(store, session_id, "assistant", content, tool_calls or None)

    # Record token consumption (best-effort).
    input_tokens, output_tokens = _extract_usage_from_result(result)
    if input_tokens or output_tokens:
        from agentcore.runtime.token_router import record_token_usage

        record_token_usage(agent_id, input_tokens, output_tokens)

    return ChatResponse(
        session_id=session_id,
        agent_id=agent_id,
        content=content,
        timestamp=_now_iso(),
        tool_calls=tool_calls,
    )


@router.post("/{agent_id}/sessions/{session_id}/approval/stream")
async def submit_approval_stream(
    agent_id: str,
    session_id: str,
    approval: ApprovalRequest,
    req: Request,
) -> StreamingResponse:
    """Submit a HITL approval and stream the agent's response via SSE.

    This is the streaming version of submit_approval — it resumes the
    agent graph and streams the output using the same SSE format as
    the main chat stream endpoint.
    """
    store = _get_store(req)

    # Validate session exists.
    session = store.load_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id!r} not found",
        )

    # Resolve the cached agent graph.
    chat_state = get_chat_state(req.app.state)
    agent_graph = chat_state.graph_cache.get(agent_id)
    if agent_graph is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Agent graph for {agent_id!r} is not cached. "
                "The session may have expired."
            ),
        )

    if chat_state.checkpointer is None:
        raise HTTPException(
            status_code=503,
            detail="Checkpointer not available — cannot resume interrupted session.",
        )

    # --- Enforce the interrupt policy --------------------------------
    if approval.decision == "respond":
        raise HTTPException(
            status_code=501,
            detail="The 'respond' decision is not supported yet.",
        )
    allowed_decisions = await _get_interrupt_allowed_decisions(agent_graph, session_id)
    if allowed_decisions and approval.decision not in allowed_decisions:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Decision {approval.decision!r} is not allowed (allowed: {sorted(allowed_decisions)})."
            ),
        )

    # --- Build the SDK decision objects for ALL pending tool calls -----
    all_pending_tools = await _get_all_interrupt_tools(agent_graph, session_id)
    if not all_pending_tools:
        all_pending_tools = [{"name": "", "args": {}}]

    decisions: list[dict[str, Any]] = []
    for tool in all_pending_tools:
        decision: dict[str, Any]
        if approval.decision == "approve":
            decision = {"type": "approve"}
        elif approval.decision == "edit":
            if not approval.edited_args:
                raise HTTPException(
                    status_code=400,
                    detail="edited_args is required for edit decision",
                )
            if len(decisions) == 0:
                decision = {
                    "type": "edit",
                    "edited_action": {
                        "name": tool.get("name", ""),
                        "args": approval.edited_args,
                    },
                }
            else:
                decision = {"type": "approve"}
        elif approval.decision == "reject":
            decision = {
                "type": "reject",
                "message": approval.message or "",
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown decision type: {approval.decision!r}",
            )
        decisions.append(decision)

    # --- Audit the decision ------------------------------------------
    tool_name_for_audit = all_pending_tools[0].get("name") or "unknown"
    final_args = (
        approval.edited_args
        if approval.decision == "edit" and approval.edited_args
        else all_pending_tools[0].get("args", {})
    )
    from agentcore.runtime.audit import record_approval_decision
    record_approval_decision(
        agent_id=agent_id,
        session_id=session_id,
        decision=approval.decision,
        tool_name=tool_name_for_audit,
        final_args=final_args,
        operator=approval.operator,
    )

    # --- Stream the resumed graph ------------------------------------
    config = {"configurable": {"thread_id": session_id}}
    resume_value = {"decisions": decisions}

    return StreamingResponse(
        _stream_approval_sse(
            agent_graph=agent_graph,
            config=config,
            resume_value=resume_value,
            agent_id=agent_id,
            session_id=session_id,
            store=store,
            decision=approval.decision,
            tool_name_probe=tool_name_for_audit,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _stream_approval_sse(
    agent_graph: Any,
    config: dict[str, Any],
    resume_value: dict[str, Any],
    agent_id: str,
    session_id: str,
    store: Any,
    decision: str,
    tool_name_probe: str,
) -> AsyncIterator[str]:
    """Stream the agent's response after approval via SSE."""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    latest_todos: list[dict[str, Any]] = []
    total_input = 0
    total_output = 0
    current_todo_idx: int = -1
    # Same heuristic as the main stream: once the agent manages todo
    # statuses itself, disable the auto-advance.
    agent_managed_todos = False

    # Streaming tool-call accumulator (same pattern as main stream).
    pending_tool_chunks: dict[int, dict[str, Any]] = {}
    emitted_tool_call_keys: set[str] = set()

    def _process_tool_call_chunks_approval(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge tool-call chunk deltas; return calls that became complete."""
        nonlocal latest_todos, current_todo_idx, agent_managed_todos
        completed: list[dict[str, Any]] = []
        for tc in chunks:
            if not isinstance(tc, dict):
                continue
            idx = tc.get("index") or 0
            acc = pending_tool_chunks.setdefault(idx, {"name": "", "args": "", "id": None})
            if tc.get("name"):
                acc["name"] = tc["name"]
            if tc.get("id"):
                acc["id"] = tc["id"]
            acc["args"] += tc.get("args") or ""
            key = acc["id"] or f"{acc['name']}@{idx}"
            if key in emitted_tool_call_keys:
                continue
            try:
                parsed_args = json.loads(acc["args"]) if acc["args"] else None
            except (json.JSONDecodeError, ValueError):
                continue
            if parsed_args is None or not acc["name"]:
                continue
            emitted_tool_call_keys.add(key)
            completed.append({"name": acc["name"], "args": parsed_args})
            if acc["name"] == "write_todos":
                todos = parsed_args.get("todos", []) if isinstance(parsed_args, dict) else []
                if todos:
                    if any(t.get("status") not in (None, "pending") for t in todos):
                        agent_managed_todos = True
                    if not agent_managed_todos:
                        for todo in todos:
                            if todo.get("status") == "pending":
                                todo["status"] = "in_progress"
                                current_todo_idx = todos.index(todo)
                                break
                    latest_todos = todos
        return completed

    def _advance_todo_status_approval() -> list[dict[str, Any]] | None:
        """Advance todo status based on tool execution (approval stream)."""
        nonlocal latest_todos, current_todo_idx
        if agent_managed_todos or not latest_todos:
            return None
        if 0 <= current_todo_idx < len(latest_todos):
            latest_todos[current_todo_idx]["status"] = "completed"
        next_idx = -1
        for i, todo in enumerate(latest_todos):
            if todo.get("status") == "pending":
                next_idx = i
                break
        if next_idx >= 0:
            latest_todos[next_idx]["status"] = "in_progress"
            current_todo_idx = next_idx
        else:
            current_todo_idx = -1
        return latest_todos

    def _persist_partial() -> None:
        """Persist whatever was produced so far (best-effort)."""
        text = "".join(text_parts).strip()
        reasoning = "".join(reasoning_parts).strip()
        if not (text or reasoning or tool_calls or latest_todos):
            return
        try:
            _append_assistant_turn(
                store, session_id, text_parts, reasoning_parts,
                tool_calls, latest_todos or None, [], None,
            )
        except Exception:
            logger.exception(
                "Failed to persist assistant message after approval for session %s",
                session_id,
            )

    try:
        async for chunk in agent_graph.astream(
            Command(resume=resume_value),
            config=config,
            stream_mode=["updates", "messages", "values"],
        ):
            if not isinstance(chunk, (list, tuple)) or len(chunk) < 2:
                continue

            mode, data = chunk[0], chunk[1]

            if mode == "messages":
                if isinstance(data, (list, tuple)) and len(data) >= 1:
                    msg_chunk = data[0]
                    content = ""
                    if hasattr(msg_chunk, "content"):
                        content = msg_chunk.content or ""
                    elif isinstance(msg_chunk, dict):
                        content = msg_chunk.get("content", "")
                    if content:
                        text_parts.append(content)
                        yield _sse_event("messages", {
                            "content": content,
                            "agent_id": agent_id,
                        })

                    reasoning = _extract_chunk_reasoning(msg_chunk)
                    if reasoning:
                        reasoning_parts.append(reasoning)
                        yield _sse_event("messages", {
                            "reasoning": reasoning,
                            "agent_id": agent_id,
                        })

                    # Early tool_calls detection — merge fragmented chunks
                    # and emit only complete calls (same as main stream).
                    raw_chunks = getattr(msg_chunk, "tool_call_chunks", None)
                    if raw_chunks is None and isinstance(msg_chunk, dict):
                        raw_chunks = msg_chunk.get("tool_call_chunks")
                    if raw_chunks:
                        completed_calls = _process_tool_call_chunks_approval(list(raw_chunks))
                        card_calls = [
                            c for c in completed_calls if c["name"] != "write_todos"
                        ]
                        if card_calls:
                            yield _sse_event("tool_calls", {
                                "tool_calls": card_calls,
                                "agent_id": agent_id,
                            })
                        for call in completed_calls:
                            if call["name"] == "write_todos":
                                args = call["args"]
                                todos_payload = (
                                    args.get("todos", [])
                                    if isinstance(args, dict) else []
                                )
                                if todos_payload:
                                    yield _sse_event("todo", {
                                        "todos": todos_payload,
                                        "agent_id": agent_id,
                                    })

                    usage = _extract_chunk_usage(
                        msg_chunk, data[1] if len(data) > 1 else None
                    )
                    if usage:
                        total_input += usage["input_tokens"]
                        total_output += usage["output_tokens"]
                        yield _sse_event("token_usage", {
                            "input_tokens": total_input,
                            "output_tokens": total_output,
                            "agent_id": agent_id,
                        })

            elif mode == "updates":
                if isinstance(data, dict):
                    for node_name, node_output in data.items():
                        if node_name == "task" or "subagent" in node_name.lower():
                            yield _sse_event("subagents", {
                                "node": node_name,
                                "update": _serialise_node_output(node_output),
                                "agent_id": agent_id,
                            })
                        if isinstance(node_output, dict):
                            msgs = node_output.get("messages", [])
                            for m in msgs:
                                tc = getattr(m, "tool_calls", None) or (
                                    m.get("tool_calls") if isinstance(m, dict) else None
                                )
                                if tc:
                                    calls = [
                                        {
                                            "name": c.get("name", "") if isinstance(c, dict) else getattr(c, "name", ""),
                                            "args": c.get("args", {}) if isinstance(c, dict) else getattr(c, "args", {}),
                                        }
                                        for c in tc
                                    ]
                                    tool_calls.extend(calls)
                                    for call in calls:
                                        if call.get("name") == "write_todos":
                                            todos = (call.get("args", {}) or {}).get("todos", [])
                                            if todos:
                                                if any(t.get("status") not in (None, "pending") for t in todos):
                                                    agent_managed_todos = True
                                                # Mark the first pending todo as in_progress
                                                # only when the agent does not manage statuses.
                                                if not agent_managed_todos:
                                                    for todo in todos:
                                                        if todo.get("status") == "pending":
                                                            todo["status"] = "in_progress"
                                                            current_todo_idx = todos.index(todo)
                                                            break
                                                latest_todos = todos
                                                yield _sse_event("todo", {
                                                    "todos": todos,
                                                    "agent_id": agent_id,
                                                })
                                    yield _sse_event("subagents", {
                                        "node": node_name,
                                        "tool_calls": calls,
                                        "agent_id": agent_id,
                                    })
                                    # Advance todo status based on tool execution.
                                    # write_todos itself does not advance progress.
                                    if any(c.get("name") != "write_todos" for c in calls):
                                        updated_todos = _advance_todo_status_approval()
                                        if updated_todos:
                                            yield _sse_event("todo", {
                                                "todos": updated_todos,
                                                "agent_id": agent_id,
                                            })

            elif mode == "values":
                if isinstance(data, dict):
                    interrupts = data.get(_INTERRUPT_KEY, [])
                    if interrupts:
                        approval_info = _build_approval_request_info(interrupts)
                        yield _sse_event("interrupts", {
                            "status": "pending_approval",
                            "approval_request": approval_info,
                            "agent_id": agent_id,
                            "session_id": session_id,
                        })

    except Exception as exc:
        logger.exception("Streaming error after approval for agent %s", agent_id)
        _persist_partial()
        yield _sse_event("error", {
            "detail": _friendly_stream_error(exc),
            "agent_id": agent_id,
        })
        return
    finally:
        _persist_partial()

    # Record the decision on the message.
    _mark_last_pending_approval(
        store, session_id, decision, tool_name_probe or "",
        clear_request=True,
    )

    # Record token consumption.
    if total_input or total_output:
        from agentcore.runtime.token_router import record_token_usage
        record_token_usage(agent_id, total_input, total_output)

    yield _sse_event("done", {
        "agent_id": agent_id,
        "session_id": session_id,
        "input_tokens": total_input,
        "output_tokens": total_output,
    })


@router.get("/{agent_id}/sessions/{session_id}/approval/status")
async def get_approval_status(
    agent_id: str,
    session_id: str,
    req: Request,
) -> dict[str, Any]:
    """Check whether a session is still waiting for approval.

    Returns the current interrupt state (if any) so the frontend can
    poll without sending a full chat message.
    """
    chat_state = get_chat_state(req.app.state)
    agent_graph = chat_state.graph_cache.get(agent_id)
    if agent_graph is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent graph for {agent_id!r} is not cached.",
        )

    config = {"configurable": {"thread_id": session_id}}
    try:
        snapshot = await agent_graph.aget_state(config)
    except Exception as exc:
        logger.exception("Failed to get state for session %s", session_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get session state: {exc}",
        ) from exc

    interrupts = list(snapshot.interrupts) if snapshot.interrupts else []
    if not interrupts:
        return {"status": "complete", "approval_request": None}

    approval_info = _build_approval_request_info(interrupts)
    return {"status": "pending_approval", "approval_request": approval_info}


async def _aget_interrupt_payloads(
    agent_graph: Any, session_id: str
) -> list[Any]:
    """Async-safe extraction of current interrupt payload values.

    The production checkpointer is an ``AsyncSqliteSaver`` whose *sync*
    ``get_state`` raises ``NotImplementedError`` — everything must go
    through ``aget_state``. Values may be dicts or pydantic ``HITLRequest``
    objects depending on the serialisation path; normalise to dicts.
    """
    payloads: list[Any] = []
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await agent_graph.aget_state(config)
    for interrupt_obj in (snapshot.interrupts or []):
        value = getattr(interrupt_obj, "value", None)
        if isinstance(value, dict):
            payloads.append(value)
        elif value is not None and hasattr(value, "model_dump"):
            try:
                payloads.append(value.model_dump())
            except Exception:
                logger.debug("Could not serialise interrupt payload %r", value)
    return payloads


async def _get_interrupt_allowed_decisions(
    agent_graph: Any, session_id: str
) -> set[str] | None:
    """Union of allowed decisions across the session's current interrupts.

    Returns ``None`` when no policy information is visible (callers then
    skip the enforcement check, preserving legacy behaviour).
    """
    try:
        allowed: set[str] = set()
        seen_policy = False
        for value in await _aget_interrupt_payloads(agent_graph, session_id):
            for rc in value.get("review_configs", []):
                decisions = (
                    rc.get("allowed_decisions") if isinstance(rc, dict) else None
                )
                if decisions:
                    seen_policy = True
                    allowed.update(decisions)
        return allowed if seen_policy else None
    except Exception:
        logger.warning("Could not extract allowed decisions from interrupt state", exc_info=True)
        return None


async def _get_first_interrupt_tool_name(
    agent_graph: Any, session_id: str
) -> str | None:
    """Best-effort extraction of the first interrupted tool name."""
    try:
        for value in await _aget_interrupt_payloads(agent_graph, session_id):
            actions = value.get("action_requests", [])
            if actions:
                first = actions[0]
                return first.get("name") if isinstance(first, dict) else getattr(first, "name", None)
    except Exception:
        logger.debug("Could not extract tool name from interrupt state")
    return None


async def _get_all_interrupt_tools(
    agent_graph: Any, session_id: str
) -> list[dict[str, Any]]:
    """Extract all interrupted tool names and args.

    Returns a list of dicts with 'name' and 'args' keys for each
    pending tool call — the count MUST match the number of decisions
    sent on resume, otherwise the SDK raises a ValueError.
    """
    tools: list[dict[str, Any]] = []
    try:
        for value in await _aget_interrupt_payloads(agent_graph, session_id):
            for action in value.get("action_requests", []):
                if isinstance(action, dict):
                    tools.append({
                        "name": action.get("name", ""),
                        "args": action.get("args", {}),
                    })
                else:
                    tools.append({
                        "name": getattr(action, "name", ""),
                        "args": getattr(action, "args", {}) or {},
                    })
    except Exception:
        logger.warning("Could not extract tools from interrupt state", exc_info=True)
    return tools


def _get_first_interrupt_args(
    agent_graph: Any, session_id: str
) -> dict[str, Any]:
    """Best-effort extraction of the first interrupted tool args."""
    try:
        config = {"configurable": {"thread_id": session_id}}
        snapshot = agent_graph.get_state(config)
        for interrupt_obj in (snapshot.interrupts or []):
            value = getattr(interrupt_obj, "value", None)
            if isinstance(value, dict):
                actions = value.get("action_requests", [])
                if actions:
                    return actions[0].get("args", {})
    except Exception:
        logger.debug("Could not extract tool args from interrupt state")
    return {}


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
    # Legacy sessions were persisted before streamed text was normalised
    # (each token chunk ended with a newline) — clean up on read so the
    # history view renders properly without rewriting stored data.
    cleaned: list[dict] = []
    for msg in recent:
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
            msg = dict(msg)
            msg["content"] = _normalize_assistant_text(msg["content"])
            reasoning = msg.get("reasoning")
            if isinstance(reasoning, str):
                msg["reasoning"] = _normalize_assistant_text(reasoning)
        cleaned.append(msg)
    return cleaned


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
