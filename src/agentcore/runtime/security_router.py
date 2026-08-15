"""Global security settings router.

Security settings are persisted in ``.agentcore/security.json`` — a
global, instance-wide configuration that every agent obeys.  The danger
operation approval (HITL) rules configured here are merged into each
agent's ``interrupt_on`` at graph-build time, so a tool listed here
requires human approval no matter which agent invokes it.

Shape of ``security.json``::

    {
        "approval": {
            "enabled": true,
            "tools": ["delete", "write_file", "execute"]
        }
    }

Endpoints
---------
* ``GET /api/security/settings`` — read the current security settings
* ``PUT /api/security/settings`` — replace the security settings
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agentcore.runtime import paths

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/security", tags=["security"])

#: Default settings — approval disabled until the user opts in.
DEFAULT_SECURITY: dict[str, Any] = {
    "approval": {
        "enabled": False,
        "tools": [],
    }
}

#: Maximum number of approval tools accepted in one update.
MAX_APPROVAL_TOOLS = 50


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _security_path() -> Any:
    return paths.get_data_dir() / "security.json"


def load_security_settings() -> dict[str, Any]:
    """Load the persisted security settings (defaults when missing)."""
    path = _security_path()
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_SECURITY))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read %s — using defaults", path)
        return json.loads(json.dumps(DEFAULT_SECURITY))
    if not isinstance(raw, dict):
        return json.loads(json.dumps(DEFAULT_SECURITY))

    approval = raw.get("approval")
    if not isinstance(approval, dict):
        approval = {}
    tools = approval.get("tools", [])
    if not isinstance(tools, list):
        tools = []
    allowed = approval.get("allowed_decisions")
    if allowed is not None and not isinstance(allowed, list):
        allowed = None
    result: dict[str, Any] = {
        "approval": {
            "enabled": bool(approval.get("enabled", False)),
            "tools": [str(t) for t in tools if str(t).strip()],
        }
    }
    if allowed:
        result["approval"]["allowed_decisions"] = [
            str(d) for d in allowed if str(d).strip()
        ]
    return result


def _save_security_settings(settings: dict[str, Any]) -> None:
    """Atomically persist the security settings to ``security.json``."""
    path = _security_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp", dir=str(path.parent), prefix=f".{path.stem}_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        if path.exists():
            path.unlink()
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Global approval rule helpers (shared with graph building)
# ---------------------------------------------------------------------------


def load_approval_interrupt_rules() -> list[dict[str, Any]]:
    """Global HITL rules every agent must obey.

    Returns ``interrupt_rules``-shaped dicts (``tool_name`` +
    ``require_approval``) derived from the global security settings, or an
    empty list when approval is disabled or no tools are selected.
    """
    settings = load_security_settings()
    approval = settings.get("approval", {})
    if not approval.get("enabled"):
        return []
    allowed = approval.get("allowed_decisions")
    if allowed is not None and not isinstance(allowed, list):
        allowed = None
    rules: list[dict[str, Any]] = []
    for tool in approval.get("tools", []):
        if not tool.strip():
            continue
        rule: dict[str, Any] = {
            "tool_name": tool,
            "require_approval": True,
        }
        if allowed:
            rule["allowed_decisions"] = list(allowed)
        rules.append(rule)
    return rules


def apply_global_approval(settings: dict[str, Any]) -> dict[str, Any]:
    """Merge the global approval rules into a settings dict.

    Agent-level ``interrupt_rules`` win on conflicts (same ``tool_name``);
    global rules fill in every tool not already covered, so the global
    configuration applies to all agents while keeping per-agent overrides
    backward compatible.
    """
    agent_rules = list(settings.get("interrupt_rules", []) or [])
    global_rules = load_approval_interrupt_rules()
    if not global_rules:
        return settings
    known = {
        r.get("tool_name")
        for r in agent_rules
        if r.get("tool_name")
    }
    merged = agent_rules + [
        r for r in global_rules if r.get("tool_name") not in known
    ]
    if len(merged) == len(agent_rules):
        return settings
    return {**settings, "interrupt_rules": merged}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/settings")
async def get_security_settings() -> dict[str, Any]:
    """Read the global security settings."""
    return load_security_settings()


@router.put("/settings")
async def update_security_settings(
    body: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """Replace the global security settings.

    Only the ``approval`` section is accepted; anything else is ignored.
    When approval rules change, every cached agent graph is invalidated so
    the next chat is built with the new global configuration.
    """
    raw_approval = body.get("approval")
    if not isinstance(raw_approval, dict):
        raise HTTPException(
            status_code=400,
            detail="Body must include an 'approval' object",
        )

    enabled = bool(raw_approval.get("enabled", False))
    raw_tools = raw_approval.get("tools", [])
    if not isinstance(raw_tools, list):
        raise HTTPException(status_code=400, detail="'tools' must be a list")
    if len(raw_tools) > MAX_APPROVAL_TOOLS:
        raise HTTPException(
            status_code=400,
            detail=f"'tools' cannot exceed {MAX_APPROVAL_TOOLS} entries",
        )
    tools: list[str] = []
    for tool in raw_tools:
        if not isinstance(tool, str) or not tool.strip():
            raise HTTPException(
                status_code=400,
                detail="Every approval tool must be a non-empty string",
            )
        if tool.strip() not in tools:
            tools.append(tool.strip())

    settings = {"approval": {"enabled": enabled, "tools": tools}}

    # Optional global decision whitelist — restricts which approval
    # decisions operators may submit for every tool listed above.
    raw_allowed = raw_approval.get("allowed_decisions")
    if raw_allowed is not None:
        if not isinstance(raw_allowed, list) or not raw_allowed:
            raise HTTPException(
                status_code=400,
                detail="'allowed_decisions' must be a non-empty list",
            )
        valid = {"approve", "edit", "reject", "respond"}
        cleaned: list[str] = []
        for d in raw_allowed:
            if not isinstance(d, str) or d not in valid:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid decision {d!r} — must be one of "
                        f"{sorted(valid)}"
                    ),
                )
            if d not in cleaned:
                cleaned.append(d)
        settings["approval"]["allowed_decisions"] = cleaned

    _save_security_settings(settings)

    # Global approval rules affect every agent graph — drop the cache so
    # the next chat rebuilds with the merged configuration.
    from agentcore.runtime.chat_router import invalidate_agent_graph

    try:
        await invalidate_agent_graph(request.app.state)
    except Exception:
        logger.exception("Failed to invalidate agent graph cache")

    logger.info(
        "Security settings updated: approval=%s tools=%s",
        enabled,
        tools,
    )
    return settings
