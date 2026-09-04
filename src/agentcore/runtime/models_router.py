"""Model management router.

Aggregates the model configurations used across all agents (each agent's
workspace ``agent.json`` carries a ``model`` section) and provides a
connectivity test endpoint, plus a user-managed *model registry* (see
:mod:`agentcore.runtime.model_store`) that the console Models page edits:
OpenAI-compatible endpoints, custom headers, extra generation parameters
and a single configurable default model.

Endpoints
---------
* ``GET  /api/models``                       — all distinct model configs in use
* ``GET  /api/models/library``               — the model registry (keys masked)
* ``POST /api/models``                       — add a registry model
* ``PUT  /api/models/{model_id}``            — update a registry model
* ``DELETE /api/models/{model_id}``          — remove a registry model
* ``PUT  /api/models/{model_id}/default``    — set/clear the default model
* ``POST /api/models/test``                  — test connectivity (with headers/extra_body)
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
from agentcore.runtime import model_store
from agentcore.runtime.agent_ids import known_agent_ids
from agentcore.runtime.model_factory import forced_gateway_headers

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


def _friendly_probe_error(exc: Exception) -> str:
    """Translate common upstream failures into actionable hints.

    Keeps a truncated copy of the raw upstream message so debugging
    stays possible while the leading sentence tells the user what to do.
    """
    text = str(exc)
    raw = text if len(text) <= 200 else f"{text[:200]}…"
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return (
            f"认证失败（{status}）：API Key 无效、过期或无权限访问该端点，"
            f"请检查密钥。上游信息：{raw}"
        )
    if status == 429 or "RateLimitError" in type(exc).__name__:
        lowered = text.lower()
        if "quota" in lowered or "insufficient" in lowered:
            return (
                "该模型配额已用尽（429）：服务商侧的额度/窗口限制已触顶"
                "（免费模型常见，如每 5 小时滚动额度），请等待额度刷新或更换模型。"
                f"上游信息：{raw}"
            )
        return f"触发服务商限流（429）：请求过于频繁，请稍后重试。上游信息：{raw}"
    if status == 404:
        return f"端点或模型不存在（404）：请核对 Base URL 与模型名。上游信息：{raw}"
    return text


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


# ---------------------------------------------------------------------------
# Model registry (user-managed library)
# ---------------------------------------------------------------------------


def _mask_model(entry: dict[str, Any]) -> dict[str, Any]:
    """Expose a registry entry without the plaintext API key.

    ``api_key_set`` reflects either a stored plaintext key or a resolvable
    env-var reference (``api_key_env``).
    """
    masked = {k: entry.get(k) for k in model_store.PUBLIC_FIELDS}
    masked["api_key_set"] = bool(entry.get("api_key")) or bool(
        _resolve_api_key(entry.get("api_key_env"))
    )
    masked["agents"] = []
    return masked


@router.get("/library")
async def list_model_library() -> dict[str, Any]:
    """List the user-managed model registry (API keys masked)."""
    models = [_mask_model(m) for m in model_store.load_model_registry()]
    return {"models": models, "count": len(models)}


class ModelRegistryPayload(BaseModel):
    """Payload for creating / updating a registry model."""

    name: str
    provider: str | None = None
    base_url: str
    api_key: str | None = None
    api_key_env: str | None = None
    headers: list[dict[str, str]] | None = None
    extra_body: dict[str, Any] | None = None
    is_default: bool | None = None


@router.post("")
async def create_registry_model(payload: ModelRegistryPayload) -> dict[str, Any]:
    """Add a model to the registry."""
    try:
        entry = model_store.sanitize_model_entry(payload.model_dump())
        stored = model_store.upsert_model(entry)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("models: added %s (%s)", stored["name"], stored["id"])
    return _mask_model(stored)


@router.put("/{model_id}")
async def update_registry_model(
    model_id: str, payload: ModelRegistryPayload
) -> dict[str, Any]:
    """Update a registry model (full replace of the editable fields)."""
    existing = next(
        (m for m in model_store.load_model_registry() if m.get("id") == model_id),
        None,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Model {model_id!r} not found")
    try:
        entry = model_store.sanitize_model_entry(payload.model_dump())
        stored = model_store.upsert_model(entry, model_id=model_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("models: updated %s (%s)", stored["name"], stored["id"])
    return _mask_model(stored)


@router.delete("/{model_id}")
async def delete_registry_model(model_id: str) -> dict[str, Any]:
    """Remove a registry model; the default flag moves to no one."""
    if not model_store.delete_model(model_id):
        raise HTTPException(status_code=404, detail=f"Model {model_id!r} not found")
    logger.info("models: deleted %s", model_id)
    return {"result": "ok", "id": model_id}


class DefaultModelPayload(BaseModel):
    """Payload for toggling the default model."""

    is_default: bool = True


@router.put("/{model_id}/default")
async def set_registry_default(
    model_id: str, payload: DefaultModelPayload
) -> dict[str, Any]:
    """Mark a registry model as the default (mutually exclusive)."""
    stored = model_store.set_default_model(model_id, payload.is_default)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"Model {model_id!r} not found")
    logger.info("models: default=%s %s", stored["name"], payload.is_default)
    return _mask_model(stored)


class ModelTestRequest(BaseModel):
    """Payload for a model connectivity test.

    ``api_key`` may carry the plaintext key directly (registry models);
    ``headers`` / ``extra_body`` are forwarded to the OpenAI client so the
    exact production configuration is exercised.
    """

    provider: str | None = None
    name: str
    base_url: str | None = None
    api_key_env: str | None = None
    api_key: str | None = None
    headers: list[dict[str, str]] | None = None
    extra_body: dict[str, Any] | None = None


@router.post("/test")
async def test_model(payload: ModelTestRequest) -> dict[str, Any]:
    """Test connectivity to the model endpoint.

    Uses an OpenAI-compatible ``chat.completions`` probe (a 1-token ping)
    so custom headers / extra_body are exercised exactly as production
    calls would be; the available model list is fetched best-effort via
    ``GET {base_url}/models``.  The API key is taken from ``api_key`` or
    resolved from ``api_key_env`` / the standard fallback env vars.
    """
    base_url = (payload.base_url or "").strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required")

    api_key = (payload.api_key or "").strip() or _resolve_api_key(payload.api_key_env)
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

    headers: dict[str, str] = {}
    for header in payload.headers or []:
        if isinstance(header, dict) and header.get("key"):
            headers[str(header["key"])] = str(header.get("value") or "")
    # Gateways behind Cloudflare bot protection (e.g. opencode.ai/zen)
    # reject the default client User-Agent with a 403; inject a browser UA.
    headers = {**forced_gateway_headers(base_url), **headers}

    def _probe() -> tuple[str, list[str], str | None]:
        kwargs: dict[str, Any] = {
            "base_url": base_url,
            "api_key": api_key,
            "timeout": 10.0,
        }
        if headers:
            kwargs["default_headers"] = headers
        client = OpenAI(**kwargs)
        try:
            completion = client.chat.completions.create(
                model=payload.name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                **(payload.extra_body or {}),
            )
            _ = completion.choices[0].message.content
        finally:
            client.close()

        # Best-effort available-model list (some gateways disable /models).
        names: list[str] = []
        try:
            probe_kwargs: dict[str, Any] = {
                "base_url": base_url,
                "api_key": api_key,
                "timeout": 10.0,
            }
            if headers:
                probe_kwargs["default_headers"] = headers
            probe_client = OpenAI(**probe_kwargs)
            try:
                page = probe_client.models.list()
                names = [getattr(m, "id", "") for m in page.data]
            finally:
                probe_client.close()
        except Exception:  # noqa: BLE001
            pass
        return "ok", names, None

    try:
        result, names, err = await asyncio.wait_for(
            asyncio.to_thread(_probe), timeout=20.0
        )
    except asyncio.TimeoutError:
        return {
            "result": "failed",
            "name": payload.name,
            "base_url": base_url,
            "error": "连接超时（20s）",
        }
    except Exception as exc:
        return {
            "result": "failed",
            "name": payload.name,
            "base_url": base_url,
            "error": _friendly_probe_error(exc),
        }

    return {
        "result": result,
        "name": payload.name,
        "base_url": base_url,
        "model_count": len(names),
        "model_found": not names or payload.name in names,
        "available_models": names[:50],
        "error": err,
    }
