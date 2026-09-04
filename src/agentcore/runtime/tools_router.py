"""Built-in tools management router.

Exposes per-agent built-in tool listing and enable/disable toggling under
``/api/agents/{agent_id}/tools``.  Built-in tool state is persisted in the
agent's workspace ``agent.json`` (``tools.disabled`` list); *capability*
tools (additive platform tools such as ``send_a2ui``) live in
``settings.<flag>`` instead.  Toggling either kind invalidates the cached
agent graph so the next chat rebuilds the agent with the updated tool set.

Endpoints
---------
* ``GET /api/agents/{agent_id}/tools``            — list tools + status
* ``PUT /api/agents/{agent_id}/tools/{tool_name}`` — enable/disable a tool
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agentcore.constants import BUILT_IN_TOOLS
from agentcore.runtime.agent_ids import known_agent_ids

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents/{agent_id}/tools", tags=["tools"])

# Human-readable descriptions for the built-in tools (display only).
TOOL_DESCRIPTIONS: dict[str, str] = {
    "ls": "列出目录内容",
    "read_file": "读取文件内容",
    "write_file": "写入文件",
    "edit_file": "编辑已有文件",
    "delete": "删除文件或目录",
    "glob": "按通配符查找文件",
    "grep": "按内容搜索文件",
    "execute": "执行 shell 命令",
    "task": "规划与管理子任务",
}

# Additive platform capability tools: tool name → (settings flag,
# default-enabled, description).  Unlike BUILT_IN_TOOLS (subtracted from
# the SDK harness via the exclusion middleware), these are appended to
# the agent by the factory when their flag is on — the flag also gates
# the matching prompt hint and SSE projection, so it is *not* stored in
# ``tools.disabled``.  (``write_todos``/planning is intentionally *not*
# listed here: its toggle lives on the Agent config page.)
CAPABILITY_TOOLS: dict[str, tuple[str, bool, str]] = {
    "send_a2ui": (
        "enable_a2ui",
        True,
        "交互式 UI 卡片（A2UI）——向用户反问、收集表单、请求确认",
    ),
}


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

    # The agent may be known (persisted state, runtime tracking or an
    # on-disk workspace directory) but its workspace not loaded yet.
    if agent_id not in known_agent_ids(request.app.state):
        raise HTTPException(status_code=404, detail="Agent not found")

    return await manager.get_or_create_workspace(agent_id)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_tools(agent_id: str, request: Request) -> dict[str, Any]:
    """List all built-in and capability tools with their enabled state."""
    workspace = await _resolve_workspace(request, agent_id)

    config = workspace.read_agent_config()
    disabled = set(config.get("tools", {}).get("disabled", []))

    tools = [
        {
            "name": tool_name,
            "enabled": tool_name not in disabled,
            "builtin": True,
            "description": TOOL_DESCRIPTIONS.get(tool_name, ""),
        }
        for tool_name in BUILT_IN_TOOLS
    ]
    settings = config.get("settings", {}) or {}
    tools.extend(
        {
            "name": tool_name,
            "enabled": bool(settings.get(flag, default)),
            "builtin": False,
            "description": desc,
        }
        for tool_name, (flag, default, desc) in CAPABILITY_TOOLS.items()
    )
    return {"agent_id": agent_id, "tools": tools}


@router.put("/{tool_name}")
async def toggle_tool(
    agent_id: str, tool_name: str, request: Request, body: dict
) -> dict[str, Any]:
    """Enable or disable a built-in or capability tool for the agent.

    Body: ``{"enabled": true | false}`` (defaults to ``true``).
    """
    if tool_name not in BUILT_IN_TOOLS and tool_name not in CAPABILITY_TOOLS:
        raise HTTPException(status_code=404, detail=f"Tool {tool_name!r} not found")

    workspace = await _resolve_workspace(request, agent_id)
    config = workspace.read_agent_config()

    enabled = bool(body.get("enabled", True))

    if tool_name in CAPABILITY_TOOLS:
        # Additive tool: flip the settings flag that gates the tool,
        # its prompt hint and its SSE projection together.
        flag, _, _ = CAPABILITY_TOOLS[tool_name]
        config.setdefault("settings", {})[flag] = enabled
    else:
        disabled = set(config.get("tools", {}).get("disabled", []))
        if enabled:
            disabled.discard(tool_name)
        else:
            disabled.add(tool_name)
        config.setdefault("tools", {})["disabled"] = sorted(disabled)

    workspace.write_agent_config(config)

    # Drop the cached agent graph so the next chat rebuilds the agent
    # with the updated tool set.
    from agentcore.runtime.chat_router import invalidate_agent_graph

    await invalidate_agent_graph(request.app.state, agent_id)

    logger.info(
        "Tool %s %s for agent %s",
        tool_name,
        "enabled" if enabled else "disabled",
        agent_id,
    )
    return {"result": "ok", "tool": tool_name, "enabled": enabled}
