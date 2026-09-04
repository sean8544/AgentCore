"""MCP connection helpers (optional-dependency aware).

AgentCore treats MCP support as *optional*: neither ``langchain-mcp-adapters``
nor the ``mcp`` SDK is a hard requirement.  Every helper in this module
detects missing packages at call time and degrades gracefully:

* :func:`mcp_backend_available` — which client backend can be used.
* :func:`fetch_server_tools` — connect to one server and list its tools
  (async; used by the MCP router for "test connection" / tool listing).
* :func:`load_mcp_tools_blocking` — synchronous bridge used by the
  (synchronous) :class:`~agentcore.runtime.agent_factory.AgentFactory`
  to collect LangChain tool objects from all enabled servers.

Connection attempts are always time-boxed so a dead MCP server can never
stall agent creation or an HTTP request.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from typing import Any

logger = logging.getLogger(__name__)

# Time-box every MCP connection attempt — a dead server must never stall
# agent creation or an HTTP request.
MCP_CONNECT_TIMEOUT: float = 15.0


class McpBackendUnavailable(RuntimeError):
    """Raised when no MCP client backend (adapters / mcp SDK) is installed."""


def mcp_backend_available() -> str | None:
    """Return the usable MCP client backend name, or ``None``.

    ``"langchain_mcp_adapters"`` is preferred (it yields ready-to-use
    LangChain tools); the raw ``"mcp"`` SDK is the fallback (tool listing
    only, no LangChain integration).
    """
    try:
        import langchain_mcp_adapters  # noqa: F401

        return "langchain_mcp_adapters"
    except ImportError:
        pass
    try:
        import mcp  # noqa: F401

        return "mcp"
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Config mapping
# ---------------------------------------------------------------------------


def _connection_kwargs(server_cfg: dict[str, Any], backend: str) -> dict[str, Any]:
    """Map an ``mcp.json`` server entry to client connection kwargs.

    Raises :class:`ValueError` when the entry lacks the fields required by
    its transport.
    """
    transport = (server_cfg.get("transport") or "stdio").lower()
    kwargs: dict[str, Any] = {}

    if transport == "stdio":
        command = (server_cfg.get("command") or "").strip()
        if not command:
            raise ValueError("stdio transport requires a non-empty 'command'")
        parts = shlex.split(command, posix=False) if os.name == 'nt' else shlex.split(command)
        if backend == "langchain_mcp_adapters":
            kwargs.update(
                {
                    "transport": "stdio",
                    "command": parts[0],
                    "args": parts[1:],
                    "env": dict(server_cfg.get("env") or {}),
                }
            )
        else:  # raw mcp SDK — StdioServerParameters
            kwargs.update(
                {
                    "transport": "stdio",
                    "command": parts[0],
                    "args": parts[1:],
                    "env": dict(server_cfg.get("env") or {}) or None,
                }
            )
    elif transport in ("sse", "streamable_http", "http"):
        url = (server_cfg.get("url") or "").strip()
        if not url:
            raise ValueError(f"{transport} transport requires a non-empty 'url'")
        kwargs.update(
            {
                "transport": "sse" if transport == "sse" else "streamable_http",
                "url": url,
                "headers": dict(server_cfg.get("headers") or {}) or None,
            }
        )
    else:
        raise ValueError(f"Unsupported MCP transport: {transport!r}")

    return kwargs


# ---------------------------------------------------------------------------
# Async tool listing (router endpoints)
# ---------------------------------------------------------------------------


async def _fetch_via_adapters(server_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch tools through ``langchain_mcp_adapters``."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    kwargs = _connection_kwargs(server_cfg, "langchain_mcp_adapters")
    client = MultiServerMCPClient({"probe": kwargs})
    try:
        tools = await client.get_tools()
    except Exception:
        # MultiServerMCPClient may raise TaskGroup errors on connection failure.
        # Re-raise so the caller can handle it uniformly.
        raise
    return [
        {
            "name": getattr(tool, "name", "") or "",
            "description": (getattr(tool, "description", "") or "").strip(),
        }
        for tool in tools
    ]


async def _fetch_via_mcp_sdk(server_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch tools through the raw ``mcp`` SDK (session + transport CMs)."""
    from contextlib import AsyncExitStack

    from mcp import ClientSession

    kwargs = _connection_kwargs(server_cfg, "mcp")
    transport = kwargs["transport"]

    async with AsyncExitStack() as stack:
        if transport == "stdio":
            from mcp.client.stdio import StdioServerParameters, stdio_client

            params = StdioServerParameters(
                command=kwargs["command"],
                args=kwargs.get("args") or [],
                env=kwargs.get("env"),
            )
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(params)
            )
        elif transport == "sse":
            from mcp.client.sse import sse_client

            read_stream, write_stream = await stack.enter_async_context(
                sse_client(url=kwargs["url"], headers=kwargs.get("headers"))
            )
        else:
            from mcp.client.streamable_http import streamable_http_client

            read_stream, write_stream = await stack.enter_async_context(
                streamable_http_client(url=kwargs["url"], headers=kwargs.get("headers"))
            )

        session = ClientSession(read_stream, write_stream)
        await stack.enter_async_context(session)
        await session.initialize()
        result = await session.list_tools()

    tools: list[dict[str, Any]] = []
    for tool in getattr(result, "tools", []) or []:
        tools.append(
            {
                "name": getattr(tool, "name", "") or "",
                "description": (getattr(tool, "description", "") or "").strip(),
            }
        )
    return tools


async def fetch_server_tools(
    server_cfg: dict[str, Any],
    timeout: float = MCP_CONNECT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Connect to one MCP server and return its tool list.

    Returns a list of ``{"name": ..., "description": ...}`` dicts.

    Raises
    ------
    McpBackendUnavailable
        When neither ``langchain-mcp-adapters`` nor ``mcp`` is installed.
    ValueError
        When the server configuration is invalid.
    Exception
        Any connection failure (timeout, refused, bad command, …) is
        propagated to the caller, which surfaces it to the API consumer.
    """
    backend = mcp_backend_available()
    if backend is None:
        raise McpBackendUnavailable(
            "MCP 依赖未安装：请先安装 langchain-mcp-adapters 或 mcp "
            "(pip install langchain-mcp-adapters)"
        )

    fetcher = _fetch_via_adapters if backend == "langchain_mcp_adapters" else _fetch_via_mcp_sdk
    try:
        return await asyncio.wait_for(fetcher(server_cfg), timeout=timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"MCP 服务器连接超时（{timeout:.0f}s），请检查命令/地址是否正确，"
            "或首次运行时需要下载依赖导致启动较慢"
        ) from None
    except ExceptionGroup as eg:
        # anyio TaskGroup errors — extract the real cause for a cleaner message
        causes = [str(e) for e in eg.exceptions]
        raise RuntimeError(f"MCP 服务器启动失败: {'; '.join(causes)}") from None


# ---------------------------------------------------------------------------
# Synchronous bridge for AgentFactory
# ---------------------------------------------------------------------------


def load_mcp_tools_blocking(
    mcp_config: dict[str, Any],
    timeout: float = MCP_CONNECT_TIMEOUT,
) -> list[Any]:
    """Collect LangChain tool objects from all *enabled* MCP servers.

    Used by the synchronous :meth:`AgentFactory.create_agent`.  The async
    work runs in a throw-away worker thread so this is safe to call from
    inside a running event loop.  Any failure (missing packages, dead
    servers, timeouts) is logged and yields an empty/partial tool list —
    agent creation must never break because of MCP.
    """
    enabled = {
        sid: cfg
        for sid, cfg in (mcp_config or {}).items()
        if isinstance(cfg, dict) and cfg.get("enabled", True)
    }
    if not enabled:
        return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        logger.warning(
            "MCP servers configured but langchain-mcp-adapters is not "
            "installed — MCP tools will NOT be injected "
            "(pip install langchain-mcp-adapters)"
        )
        return []

    connections: dict[str, dict[str, Any]] = {}
    for sid, cfg in enabled.items():
        try:
            connections[sid] = _connection_kwargs(cfg, "langchain_mcp_adapters")
        except ValueError as exc:
            logger.warning("Skipping invalid MCP server %r: %s", sid, exc)

    if not connections:
        return []

    # Per-server timeout: each server gets MCP_CONNECT_TIMEOUT seconds.
    # Overall timeout must cover all servers sequentially.
    per_server_timeout = MCP_CONNECT_TIMEOUT
    overall_timeout = per_server_timeout * max(len(connections), 1) + 10  # buffer

    async def _gather() -> list[Any]:
        # Load tools per-server so one failing server does not kill the rest.
        # MultiServerMCPClient.get_tools() uses a TaskGroup internally —
        # a single failure aborts the whole group.  By calling each server
        # individually we get partial success.
        all_tools: list[Any] = []
        for sid, conn_kwargs in connections.items():
            try:
                single_client = MultiServerMCPClient({sid: conn_kwargs})
                tools = await asyncio.wait_for(
                    single_client.get_tools(), timeout=per_server_timeout
                )
                all_tools.extend(tools)
            except Exception as exc:
                logger.warning(
                    "MCP server %r failed to load tools (skipped): %s",
                    sid, exc,
                )
        return all_tools

    def _run() -> list[Any]:
        return asyncio.run(asyncio.wait_for(_gather(), timeout=overall_timeout))

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            tools = _run()
        else:
            # Inside a running loop (FastAPI) — run in a worker thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                tools = pool.submit(_run).result(timeout=overall_timeout + 10)
        logger.info("Injected %d MCP tool(s) from %d server(s)", len(tools), len(connections))
        return tools
    except ExceptionGroup as eg:
        # anyio TaskGroup errors — log and return empty (best-effort)
        causes = [str(e) for e in eg.exceptions]
        logger.warning(
            "MCP tool loading failed — agent will start without MCP tools: %s",
            "; ".join(causes),
        )
        return []
    except Exception as exc:  # noqa: BLE001 — MCP must never break agent creation
        logger.warning("MCP tool loading failed — agent will start without MCP tools: %s", exc)
        return []
