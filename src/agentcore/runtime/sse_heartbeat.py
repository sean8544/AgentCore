# -*- coding: utf-8 -*-
"""SSE keep-alive heartbeat wrapper for async generators.

Long-running SSE streams (chat / HITL approval) can sit completely idle
for minutes — e.g. while a human approval decision is pending — and idle
connections are prone to being dropped by browsers or reverse proxies.
This module wraps an async generator so that a keep-alive chunk is
emitted whenever the source stays silent for longer than the heartbeat
interval (port of QwenPaw's ``runtime/heartbeat.py`` pattern).

Usage::

    return StreamingResponse(
        keepalive_sse(_stream_chat_sse(...)),
        media_type="text/event-stream",
    )

The keep-alive chunk is a standard SSE comment line (``: keep-alive``),
which EventSource clients silently ignore — no frontend changes needed.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

#: Idle seconds before a keep-alive chunk is emitted.
HEARTBEAT_INTERVAL_SECONDS = 25.0

#: Sentinel yielded by :func:`iter_with_heartbeat` on idle ticks.
_HEARTBEAT_TICK = object()

#: SSE comment line — ignored by EventSource clients, keeps the
#: connection (and any intermediate proxy timers) alive.
SSE_KEEPALIVE_CHUNK = ": keep-alive\n\n"


async def iter_with_heartbeat(
    source_iter: AsyncIterator[Any],
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> AsyncIterator[Any]:
    """Wrap *source_iter* so it yields ``_HEARTBEAT_TICK`` while idle.

    Uses ``asyncio.shield`` so that ``wait_for``'s cancellation on
    timeout does NOT cancel the underlying ``__anext__()`` task — that
    task lives across heartbeats and is awaited again on the next loop
    iteration.  Without shielding, a long idle period (e.g. waiting for
    a HITL approval) would lose pending state every heartbeat.
    """
    source_iter = source_iter.__aiter__()
    pending: asyncio.Future | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(source_iter.__anext__())
            try:
                value = await asyncio.wait_for(
                    asyncio.shield(pending),
                    timeout=interval,
                )
            except asyncio.TimeoutError:
                yield _HEARTBEAT_TICK
                continue
            except StopAsyncIteration:
                pending = None
                return
            pending = None
            yield value
    finally:
        if pending is not None and not pending.done():
            pending.cancel()


async def keepalive_sse(
    source_iter: AsyncIterator[Any],
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
    keepalive_chunk: str = SSE_KEEPALIVE_CHUNK,
) -> AsyncIterator[Any]:
    """Pass *source_iter* through, injecting *keepalive_chunk* on idle.

    Designed to wrap the outermost SSE generator handed to
    ``StreamingResponse`` so every internal stall (graph invocation,
    interrupt waits, persistence) is covered.
    """
    async for item in iter_with_heartbeat(source_iter, interval):
        if item is _HEARTBEAT_TICK:
            yield keepalive_chunk
        else:
            yield item
