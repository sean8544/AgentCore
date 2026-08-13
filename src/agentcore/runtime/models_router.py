"""Model management router.

Aggregates the model configurations used across all agents (each agent's
workspace ``agent.json`` carries a ``model`` section) and provides a
connectivity test endpoint.

Endpoints
---------
* ``GET  /api/models``      — all distinct model configurations in use
* ``POST /api/models/test`` — test connectivity to a model endpoint
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agentcore.runtime import paths
from agentcore.runtime.agent_ids import known_agent_ids

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])

# Env var names checked for an API key during connection tests
# (same priority order as agentcore.runtime.model_factory).
_API_KEY_ENV_VARS = (
    "AGENTCORE_LLM_API_KEY",
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
    "OPENAI_API_KEY",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_agent_json(agent_id: str) -> dict[str, Any] | None:
    """Read an agent's ``agent.json`` directly from disk."""
    path = paths.get_agent_workspace_dir(agent_id) / "agent.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read %s", path)
        return None
    return raw if isinstance(raw, dict) else None


def _known_agent_ids(request: Request) -> set[str]:
    """All agent ids known to the system (loaded, persisted, or tracked).

    Thin wrapper over the shared :func:`agentcore.runtime.agent_ids.known_agent_ids`.
    """
    return known_agent_ids(request.app.state)


def _resolve_api_key(api_key_env: str | None) -> str | None:
    """Resolve an API key from the given env var name or known fallbacks."""
    candidates: list[str] = []
    if api_key_env:
        candidates.append(api_key_env)
    candidates.extend(_API_KEY_ENV_VARS)
    for var in candidates:
        value = os.environ.get(var)
        if value and value.strip():
            return value.strip()
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_models(request: Request) -> dict[str, Any]:
    """Aggregate the model configurations used by all agents."""
    grouped: dict[tuple, dict[str, Any]] = {}

    for agent_id in sorted(_known_agent_ids(request)):
        config = _read_agent_json(agent_id)
        model_cfg = (config or {}).get("model")
        if not isinstance(model_cfg, dict) or not model_cfg.get("name"):
            continue

        provider = str(model_cfg.get("provider") or "")
        name = str(model_cfg.get("name"))
        base_url = str(model_cfg.get("base_url") or "")
        api_key_env = str(model_cfg.get("api_key_env") or "")

        entry = grouped.setdefault(
            (provider, name, base_url),
            {
                "provider": provider,
                "name": name,
                "base_url": base_url,
                "api_key_env": api_key_env,
                "api_key_set": bool(_resolve_api_key(api_key_env)),
                "agents": [],
            },
        )
        entry["agents"].append(agent_id)

    return {"models": list(grouped.values()), "count": len(grouped)}


class ModelTestRequest(BaseModel):
    """Payload for a model connectivity test."""

    provider: str | None = None
    name: str
    base_url: str | None = None
    api_key_env: str | None = None


@router.post("/test")
async def test_model(payload: ModelTestRequest) -> dict[str, Any]:
    """Test connectivity to the model endpoint (lists available models).

    Uses the OpenAI-compatible ``GET {base_url}/models`` endpoint; the API
    key is resolved from ``api_key_env`` or the standard fallback env vars.
    """
    base_url = (payload.base_url or "").strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required")

    api_key = _resolve_api_key(payload.api_key_env)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="No API key found — set AGENTCORE_LLM_API_KEY "
            "(or configure environment variables)",
        )

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="openai package is not installed",
        ) from exc

    def _probe() -> tuple[int, list[str]]:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=10.0)
        try:
            page = client.models.list()
            names = [getattr(m, "id", "") for m in page.data]
            return len(names), names
        finally:
            client.close()

    try:
        model_count, names = await asyncio.wait_for(
            asyncio.to_thread(_probe), timeout=15.0
        )
    except asyncio.TimeoutError:
        return {
            "result": "failed",
            "name": payload.name,
            "base_url": base_url,
            "error": "连接超时（15s）",
        }
    except Exception as exc:
        return {
            "result": "failed",
            "name": payload.name,
            "base_url": base_url,
            "error": str(exc),
        }

    model_found = not names or payload.name in names
    return {
        "result": "ok" if model_found else "warning",
        "name": payload.name,
        "base_url": base_url,
        "model_count": model_count,
        "model_found": model_found,
        "error": None if model_found else f"端点可用，但未找到模型 {payload.name!r}",
    }
