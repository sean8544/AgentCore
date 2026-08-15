"""Model registry store: user-managed OpenAI-compatible model library.

The model registry is persisted in ``.agentcore/models.json`` — a global,
instance-wide list of model configurations (OpenAI-compatible endpoints,
API keys, custom headers and extra generation parameters) that the console
Models page manages.  A single entry can be marked as the *default* model,
which is used when creating new agents that do not declare their own.

Shape of ``models.json``::

    {
        "models": [
            {
                "id": "qwen3-6-plus-a1b2c3",
                "name": "qwen3.6-plus",
                "provider": "openai",
                "base_url": "https://coding.dashscope.aliyuncs.com/v1",
                "api_key": "sk-...",            # optional (plaintext, local)
                "api_key_env": "AGENTCORE_LLM_API_KEY",  # optional
                "headers": [{"key": "x-app", "value": "cli"}],  # optional
                "extra_body": {"enable_thinking": false},       # optional
                "is_default": false,
                "created_at": "...",
                "updated_at": "..."
            }
        ]
    }

The store is intentionally small and dependency-free (``paths`` only) so
both the model router and the workspace layer can use it without import
cycles.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Any

from agentcore.runtime import paths

logger = logging.getLogger(__name__)

#: Maximum number of models kept in the registry.
MAX_MODELS = 100

#: Maximum number of custom header entries per model.
MAX_HEADERS = 20

#: Keys of a model entry that are safe to expose to the UI.
PUBLIC_FIELDS = (
    "id", "name", "provider", "base_url", "api_key_env",
    "headers", "extra_body", "is_default", "created_at", "updated_at",
)


def _registry_path() -> Any:
    return paths.get_data_dir() / "models.json"


def _empty_registry() -> dict[str, Any]:
    return {"models": []}


def load_model_registry() -> list[dict[str, Any]]:
    """Load the persisted model registry (empty list when missing/corrupt)."""
    path = _registry_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read %s — using empty registry", path)
        return []
    if not isinstance(raw, dict):
        return []
    models = raw.get("models")
    if not isinstance(models, list):
        return []
    return [m for m in models if isinstance(m, dict)]


def save_model_registry(models: list[dict[str, Any]]) -> None:
    """Atomically persist the model registry to ``models.json``."""
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp", dir=str(path.parent), prefix=f".{path.stem}_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"models": models}, f, ensure_ascii=False, indent=2)
        if path.exists():
            path.unlink()
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def make_model_id(name: str) -> str:
    """Derive a stable-ish unique id from a model name."""
    import hashlib

    slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "model"
    digest = hashlib.sha256(f"{name}{time.time()}".encode("utf-8")).hexdigest()[:6]
    return f"{slug}-{digest}"


def sanitize_model_entry(body: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalise a model entry payload (raise ValueError).

    Accepts the wire shape (``name`` / ``provider`` / ``base_url`` /
    ``api_key`` / ``api_key_env`` / ``headers`` / ``extra_body`` /
    ``is_default``) and returns a normalised, persisted entry.
    """
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    if len(name) > 200:
        raise ValueError("name is too long")

    base_url = str(body.get("base_url") or "").strip()
    if not base_url:
        raise ValueError("base_url is required")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url must start with http:// or https://")

    provider = str(body.get("provider") or "openai").strip() or "openai"

    api_key = body.get("api_key")
    api_key = str(api_key).strip() if isinstance(api_key, str) else ""
    api_key_env = str(body.get("api_key_env") or "").strip()

    headers = body.get("headers") or []
    if isinstance(headers, dict):
        headers = [{"key": str(k), "value": str(v)} for k, v in headers.items()]
    if not isinstance(headers, list):
        raise ValueError("headers must be a list")
    if len(headers) > MAX_HEADERS:
        raise ValueError(f"headers cannot exceed {MAX_HEADERS} entries")
    normalised_headers: list[dict[str, str]] = []
    for header in headers:
        if not isinstance(header, dict):
            continue
        key = str(header.get("key") or "").strip()
        if not key:
            continue
        normalised_headers.append({"key": key, "value": str(header.get("value") or "")})

    extra_body = body.get("extra_body")
    if extra_body is None:
        extra_body = {}
    if not isinstance(extra_body, dict):
        raise ValueError("extra_body must be a JSON object")
    if not extra_body:
        extra_body = {}

    return {
        "name": name,
        "provider": provider,
        "base_url": base_url,
        "api_key": api_key,
        "api_key_env": api_key_env,
        "headers": normalised_headers,
        "extra_body": extra_body,
        "is_default": bool(body.get("is_default", False)),
    }


def upsert_model(entry: dict[str, Any], model_id: str | None = None) -> dict[str, Any]:
    """Insert (or replace) a model entry; returns the stored entry.

    Enforces the single-default invariant: setting ``is_default`` clears
    the flag on every other entry.
    """
    registry = load_model_registry()
    now = _now_iso()

    if model_id is None or not any(m.get("id") == model_id for m in registry):
        model_id = model_id or make_model_id(entry["name"])
        stored: dict[str, Any] = {
            **entry,
            "id": model_id,
            "created_at": now,
            "updated_at": now,
        }
        if len(registry) >= MAX_MODELS:
            raise ValueError(f"registry cannot exceed {MAX_MODELS} models")
        registry.append(stored)
    else:
        stored = {
            **entry,
            "id": model_id,
            "created_at": next(
                (m.get("created_at", now) for m in registry if m.get("id") == model_id),
                now,
            ),
            "updated_at": now,
        }
        registry = [
            stored if m.get("id") == model_id else m for m in registry
        ]

    if stored.get("is_default"):
        registry = [
            {**m, "is_default": False} if m.get("id") != model_id else m
            for m in registry
        ]

    save_model_registry(registry)
    return stored


def delete_model(model_id: str) -> bool:
    """Remove a model entry; returns False when it did not exist."""
    registry = load_model_registry()
    remaining = [m for m in registry if m.get("id") != model_id]
    if len(remaining) == len(registry):
        return False
    save_model_registry(remaining)
    return True


def set_default_model(model_id: str, is_default: bool = True) -> dict[str, Any] | None:
    """Mark *model_id* as the default (mutually exclusive); None if unknown."""
    registry = load_model_registry()
    target = next((m for m in registry if m.get("id") == model_id), None)
    if target is None:
        return None
    if is_default:
        registry = [{**m, "is_default": False} for m in registry]
    registry = [
        {**m, "is_default": bool(is_default), "updated_at": _now_iso()}
        if m.get("id") == model_id else m
        for m in registry
    ]
    save_model_registry(registry)
    return next((m for m in registry if m.get("id") == model_id), None)


def get_default_model() -> dict[str, Any] | None:
    """Return the registry entry marked as default (None when none)."""
    for model in load_model_registry():
        if model.get("is_default"):
            return model
    return None


def get_default_model_config() -> dict[str, Any] | None:
    """``agent.json``-shaped ``model`` section for the default model.

    Used when seeding a new agent's ``agent.json`` so freshly created
    agents automatically follow the console's default model.  ``None``
    when no default model is configured.
    """
    model = get_default_model()
    if model is None:
        return None
    config: dict[str, Any] = {
        "provider": model.get("provider", "openai"),
        "name": model.get("name", ""),
    }
    base_url = model.get("base_url")
    if base_url:
        config["base_url"] = base_url
    api_key = model.get("api_key")
    if api_key:
        config["api_key"] = api_key
    api_key_env = model.get("api_key_env")
    if api_key_env:
        config["api_key_env"] = api_key_env
    headers = model.get("headers")
    if headers:
        config["headers"] = headers
    extra_body = model.get("extra_body")
    if extra_body:
        config["extra_body"] = extra_body
    return config
