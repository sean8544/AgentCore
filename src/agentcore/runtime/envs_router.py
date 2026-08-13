"""Environment variable management router.

Environment variables are persisted in ``.agentcore/envs.json`` (a flat
``{"KEY": "value"}`` mapping).  Values are applied to ``os.environ`` on
save so model API keys (e.g. ``AGENTCORE_LLM_API_KEY``) take effect
without a restart.

The list endpoint masks every value as ``***``; updates that send the
masked placeholder keep the previously stored value.

Endpoints
---------
* ``GET    /api/envs``         — list variables (values masked)
* ``PUT    /api/envs``         — batch upsert variables
* ``DELETE /api/envs/{key}``   — delete a variable
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from agentcore.runtime import paths

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/envs", tags=["envs"])

MASKED_VALUE = "***"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _envs_path() -> Path:
    return paths.get_data_dir() / "envs.json"


def _load_envs() -> dict[str, str]:
    """Load the persisted environment variables (empty dict when missing)."""
    path = _envs_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read %s — treating as empty", path)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _save_envs(envs: dict[str, str]) -> None:
    """Atomically persist the environment variables and apply them."""
    path = _envs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp", dir=str(path.parent), prefix=f".{path.stem}_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(envs, f, ensure_ascii=False, indent=2)
        if path.exists():
            path.unlink()
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    # Apply to the current process so newly saved keys are usable
    # immediately (e.g. LLM API keys).
    for key, value in envs.items():
        os.environ[key] = value


def load_envs() -> dict[str, str]:
    """Load persisted environment variables into ``os.environ`` at startup.

    Reads ``.agentcore/envs.json`` and applies every entry to the current
    process so persisted secrets (e.g. ``AGENTCORE_LLM_API_KEY``) are
    available to the model factory right after boot — without this, keys
    saved through ``PUT /api/envs`` would only survive until the next
    restart.

    Variables already present in ``os.environ`` are never overwritten:
    process-level configuration always wins over the persisted file.

    Returns the persisted mapping (regardless of what was applied).
    """
    envs = _load_envs()
    applied: list[str] = []
    for key, value in envs.items():
        if key not in os.environ:
            os.environ[key] = value
            applied.append(key)
    if applied:
        logger.info(
            "Loaded %d environment variable(s) from envs.json: %s",
            len(applied),
            sorted(applied),
        )
    return envs


def _masked(envs: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {"key": key, "value": MASKED_VALUE}
        for key in sorted(envs)
    ]


def _validate_key(key: str) -> None:
    if not key.strip():
        raise HTTPException(status_code=400, detail="Key cannot be empty")
    if any(ch.isspace() for ch in key):
        raise HTTPException(status_code=400, detail="Key cannot contain whitespace")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_envs() -> dict[str, Any]:
    """List all environment variables with masked values."""
    envs = _load_envs()
    return {"envs": _masked(envs), "count": len(envs)}


@router.put("")
async def update_envs(body: dict[str, Any]) -> dict[str, Any]:
    """Batch upsert environment variables.

    Values equal to ``***`` keep the previously stored value so the
    masked list endpoint can be round-tripped safely.
    """
    existing = _load_envs()
    merged = dict(existing)

    for key, value in body.items():
        _validate_key(key)
        key = key.strip()
        if value == MASKED_VALUE:
            # Masked placeholder — keep the existing value if present.
            if key in existing:
                continue
            raise HTTPException(
                status_code=400,
                detail=f"Value for new key {key!r} cannot be masked",
            )
        if value is None:
            merged.pop(key, None)
            os.environ.pop(key, None)
            continue
        merged[key] = str(value)

    _save_envs(merged)
    logger.info("Environment variables updated (%d entries)", len(merged))
    return {"result": "ok", "envs": _masked(merged), "count": len(merged)}


@router.delete("/{key}")
async def delete_env(key: str) -> dict[str, Any]:
    """Delete a single environment variable."""
    envs = _load_envs()
    if key not in envs:
        raise HTTPException(status_code=404, detail=f"Env var {key!r} not found")

    envs.pop(key, None)
    os.environ.pop(key, None)
    _save_envs(envs)
    logger.info("Environment variable %s deleted", key)
    return {"result": "ok", "envs": _masked(envs), "count": len(envs)}
