"""MCP (Model Context Protocol) server management router.

Exposes per-agent MCP server configuration under
``/api/agents/{agent_id}/mcp``.  Configuration is persisted in the agent's
workspace ``mcp.json``; enabled servers are injected as tools when the
agent graph is (re)built — see :mod:`agentcore.runtime.agent_factory`.

Endpoints
---------
* ``GET    /api/agents/{agent_id}/mcp``                  — list servers
* ``POST   /api/agents/{agent_id}/mcp``                  — add a server
* ``PUT    /api/agents/{agent_id}/mcp/{server_id}``      — update a server
* ``PATCH  /api/agents/{agent_id}/mcp/{server_id}/toggle`` — enable/disable
* ``DELETE /api/agents/{agent_id}/mcp/{server_id}``      — delete a server
* ``POST   /api/agents/{agent_id}/mcp/{server_id}/test`` — test connection
* ``GET    /api/agents/{agent_id}/mcp/{server_id}/tools`` — list server tools

Connection testing / tool listing require the optional MCP dependencies
(``langchain-mcp-adapters`` or ``mcp``).  When they are missing the
endpoints still answer — with a clear ``error`` message instead of a 500 —
so the console can display the hint.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents/{agent_id}/mcp", tags=["mcp"])

# Server ids are used as file-safe config keys and LangChain connection
# names — restrict them to a conservative identifier alphabet.
_SERVER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_VALID_TRANSPORTS = {"stdio", "sse", "streamable_http", "http"}


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


async def _resolve_workspace(request: Request, agent_id: str) -> Any:
    """Return the workspace for *agent_id*, lazily loading it when needed.

    Raises a 404 when the agent is unknown to the system.
    """
    manager = getattr(request.app.state, "agent_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="MultiAgentManager is not available.")

    workspace = manager.get_workspace(agent_id)
    if workspace is not None:
        return workspace

    store = getattr(request.app.state, "store", None)
    known = store is not None and agent_id in getattr(store, "agent_states", {})
    if not known:
        raise HTTPException(status_code=404, detail="Agent not found")

    return await manager.get_or_create_workspace(agent_id)


def _sanitize_server_entry(body: dict[str, Any], server_id: str) -> dict[str, Any]:
    """Validate and normalise a client-supplied server entry."""
    if not _SERVER_ID_RE.match(server_id):
        raise HTTPException(
            status_code=400,
            detail="server_id 只能包含字母、数字、下划线和连字符（最长 64）",
        )

    transport = (body.get("transport") or "stdio").lower()
    if transport not in _VALID_TRANSPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的传输方式 {transport!r}，可选: {sorted(_VALID_TRANSPORTS)}",
        )

    if transport == "stdio" and not (body.get("command") or "").strip():
        raise HTTPException(status_code=400, detail="stdio 传输方式必须提供 command")
    if transport in ("sse", "streamable_http", "http") and not (body.get("url") or "").strip():
        raise HTTPException(status_code=400, detail=f"{transport} 传输方式必须提供 url")

    return {
        "server_id": server_id,
        "name": body.get("name") or server_id,
        "transport": transport,
        "command": body.get("command") or "",
        "url": body.get("url") or "",
        "env": body.get("env") or {},
        "headers": body.get("headers") or {},
        "enabled": bool(body.get("enabled", True)),
    }


async def _invalidate_graph(app_state: Any, agent_id: str) -> None:
    """Drop the cached agent graph so MCP changes take effect next chat."""
    from agentcore.runtime.chat_router import invalidate_agent_graph

    try:
        await invalidate_agent_graph(app_state, agent_id)
    except Exception:  # noqa: BLE001 — invalidation is best-effort
        logger.warning("Failed to invalidate agent graph for %s", agent_id)


# ---------------------------------------------------------------------------
# Routes — CRUD
# ---------------------------------------------------------------------------


@router.get("")
async def list_mcp_servers(agent_id: str, request: Request) -> dict[str, Any]:
    """List all MCP servers configured for the agent."""
    workspace = await _resolve_workspace(request, agent_id)
    config = workspace.read_mcp_config()
    return {"agent_id": agent_id, "servers": list(config.values())}


@router.post("")
async def add_mcp_server(agent_id: str, request: Request, body: dict) -> dict[str, Any]:
    """Add (or overwrite) an MCP server configuration entry."""
    server_id = (body.get("server_id") or "").strip()
    if not server_id:
        raise HTTPException(status_code=400, detail="缺少 server_id")

    workspace = await _resolve_workspace(request, agent_id)
    entry = _sanitize_server_entry(body, server_id)

    config = workspace.read_mcp_config()
    config[server_id] = entry
    workspace.write_mcp_config(config)
    await _invalidate_graph(request.app.state, agent_id)

    logger.info("MCP server %s added for agent %s", server_id, agent_id)
    return {"result": "ok", "server": entry}


@router.put("/{server_id}")
async def update_mcp_server(
    agent_id: str, server_id: str, request: Request, body: dict
) -> dict[str, Any]:
    """Update an existing MCP server entry (404 when unknown)."""
    workspace = await _resolve_workspace(request, agent_id)
    config = workspace.read_mcp_config()
    if server_id not in config:
        raise HTTPException(status_code=404, detail=f"MCP server {server_id!r} not found")

    merged = {**config[server_id], **body}
    entry = _sanitize_server_entry(merged, server_id)
    config[server_id] = entry
    workspace.write_mcp_config(config)
    await _invalidate_graph(request.app.state, agent_id)

    logger.info("MCP server %s updated for agent %s", server_id, agent_id)
    return {"result": "ok", "server": entry}


@router.patch("/{server_id}/toggle")
async def toggle_mcp_server(
    agent_id: str, server_id: str, request: Request
) -> dict[str, Any]:
    """Toggle the enabled state of an MCP server."""
    workspace = await _resolve_workspace(request, agent_id)
    config = workspace.read_mcp_config()
    entry = config.get(server_id)
    if not isinstance(entry, dict):
        raise HTTPException(status_code=404, detail=f"MCP server {server_id!r} not found")

    entry["enabled"] = not entry.get("enabled", True)
    config[server_id] = entry
    workspace.write_mcp_config(config)
    await _invalidate_graph(request.app.state, agent_id)

    logger.info(
        "MCP server %s %s for agent %s",
        server_id,
        "enabled" if entry["enabled"] else "disabled",
        agent_id,
    )
    return {"result": "ok", "server": entry}


@router.delete("/{server_id}")
async def delete_mcp_server(
    agent_id: str, server_id: str, request: Request
) -> dict[str, Any]:
    """Delete an MCP server configuration entry."""
    workspace = await _resolve_workspace(request, agent_id)
    config = workspace.read_mcp_config()
    if server_id not in config:
        raise HTTPException(status_code=404, detail=f"MCP server {server_id!r} not found")

    del config[server_id]
    workspace.write_mcp_config(config)
    await _invalidate_graph(request.app.state, agent_id)

    logger.info("MCP server %s deleted for agent %s", server_id, agent_id)
    return {"result": "ok"}


# ---------------------------------------------------------------------------
# Routes — connection test & tool listing
# ---------------------------------------------------------------------------


@router.post("/{server_id}/test")
async def test_mcp_connection(
    agent_id: str, server_id: str, request: Request
) -> dict[str, Any]:
    """Test connectivity to an MCP server by listing its tools.

    Always answers 200; the payload carries ``ok`` / ``error`` so the
    console can display connection problems inline.
    """
    from agentcore.runtime.mcp_client import (
        McpBackendUnavailable,
        fetch_server_tools,
    )

    workspace = await _resolve_workspace(request, agent_id)
    config = workspace.read_mcp_config()
    entry = config.get(server_id)
    if not isinstance(entry, dict):
        raise HTTPException(status_code=404, detail=f"MCP server {server_id!r} not found")

    try:
        tools = await fetch_server_tools(entry)
    except McpBackendUnavailable as exc:
        return {"ok": False, "error": str(exc), "tools": []}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — surface any connect failure cleanly
        logger.warning("MCP connection test failed for %s/%s: %s", agent_id, server_id, exc)
        return {"ok": False, "error": f"连接失败: {exc}", "tools": []}

    logger.info(
        "MCP connection test succeeded for %s/%s (%d tool(s))",
        agent_id,
        server_id,
        len(tools),
    )
    return {"ok": True, "tools": tools, "tool_count": len(tools)}


@router.get("/{server_id}/tools")
async def list_mcp_tools(
    agent_id: str, server_id: str, request: Request
) -> dict[str, Any]:
    """List the tools exposed by an MCP server (connects on demand)."""
    from agentcore.runtime.mcp_client import (
        McpBackendUnavailable,
        fetch_server_tools,
    )

    workspace = await _resolve_workspace(request, agent_id)
    config = workspace.read_mcp_config()
    entry = config.get(server_id)
    if not isinstance(entry, dict):
        raise HTTPException(status_code=404, detail=f"MCP server {server_id!r} not found")

    try:
        tools = await fetch_server_tools(entry)
    except McpBackendUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MCP 连接失败: {exc}") from exc

    return {"agent_id": agent_id, "server_id": server_id, "tools": tools}
