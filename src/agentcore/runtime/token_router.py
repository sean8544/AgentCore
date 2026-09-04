"""Token usage tracking router.

Every agent invocation records its LLM token consumption (extracted from
``AIMessage.usage_metadata`` in the chat router) as an append-only record
in ``.agentcore/data/token_usage.json``.

Endpoints
---------
* ``GET /api/token-usage?agent_id=&days=7`` — aggregated usage stats
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from agentcore.runtime import paths

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/token-usage", tags=["token-usage"])

# Cap the number of stored records so the JSON file cannot grow unbounded.
_MAX_RECORDS = 10000


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _usage_path() -> Path:
    return paths.get_data_dir() / "data" / "token_usage.json"


def _load_records() -> list[dict[str, Any]]:
    path = _usage_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read %s — treating as empty", path)
        return []
    records = raw.get("records") if isinstance(raw, dict) else None
    return records if isinstance(records, list) else []


def _save_records(records: list[dict[str, Any]]) -> None:
    path = _usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp", dir=str(path.parent), prefix=f".{path.stem}_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"records": records}, f, ensure_ascii=False, indent=1)
        if path.exists():
            path.unlink()
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_token_usage(
    agent_id: str,
    input_tokens: int,
    output_tokens: int,
    model: str = "",
) -> None:
    """Append one usage record for *agent_id* (called by the chat router).

    Zero-token records (e.g. cache-only responses) are skipped.  Failures
    are logged but never break the chat flow.
    """
    if input_tokens <= 0 and output_tokens <= 0:
        return
    try:
        now = datetime.now(tz=timezone.utc)
        record = {
            "ts": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "agent_id": agent_id,
            "model": model,
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "total_tokens": int(input_tokens) + int(output_tokens),
        }
        records = _load_records()
        records.append(record)
        if len(records) > _MAX_RECORDS:
            records = records[-_MAX_RECORDS:]
        _save_records(records)
    except Exception:
        logger.exception("Failed to record token usage for agent %s", agent_id)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def get_token_usage(
    agent_id: str | None = None,
    days: int = 7,
) -> dict[str, Any]:
    """Aggregate token usage over the last *days* days.

    Returns daily breakdowns (oldest first), per-agent totals and the
    overall totals within the window.
    """
    days = max(1, min(days, 90))
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    cutoff_date = cutoff.strftime("%Y-%m-%d")

    daily: dict[str, dict[str, int]] = {}
    by_agent: dict[str, dict[str, int]] = {}
    by_model: dict[str, dict[str, int]] = {}
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "requests": 0}

    for rec in _load_records():
        date = rec.get("date", "")
        if date < cutoff_date:
            continue
        rec_agent = rec.get("agent_id", "")
        if agent_id is not None and rec_agent != agent_id:
            continue

        inp = int(rec.get("input_tokens", 0))
        out = int(rec.get("output_tokens", 0))
        tot = int(rec.get("total_tokens", inp + out))

        day = daily.setdefault(
            date,
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "requests": 0},
        )
        day["input_tokens"] += inp
        day["output_tokens"] += out
        day["total_tokens"] += tot
        day["requests"] += 1

        agg = by_agent.setdefault(
            rec_agent,
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "requests": 0},
        )
        agg["input_tokens"] += inp
        agg["output_tokens"] += out
        agg["total_tokens"] += tot
        agg["requests"] += 1

        rec_model = rec.get("model", "") or "unknown"
        model_agg = by_model.setdefault(
            rec_model,
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "requests": 0},
        )
        model_agg["input_tokens"] += inp
        model_agg["output_tokens"] += out
        model_agg["total_tokens"] += tot
        model_agg["requests"] += 1

        totals["input_tokens"] += inp
        totals["output_tokens"] += out
        totals["total_tokens"] += tot
        totals["requests"] += 1

    return {
        "days": days,
        "agent_id": agent_id,
        "totals": totals,
        "daily": [
            {"date": date, **daily[date]}
            for date in sorted(daily)
        ],
        "by_agent": [
            {"agent_id": aid, **by_agent[aid]}
            for aid in sorted(by_agent)
        ],
        "by_model": [
            {"model": mid, **by_model[mid]}
            for mid in sorted(by_model)
        ],
    }
