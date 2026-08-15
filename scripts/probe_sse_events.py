"""Probe the SSE chat stream: verify token_usage and reasoning events.

Usage: python scripts/probe_sse_events.py [base_url]
"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"


def main() -> None:
    body = json.dumps({
        "agent_id": "calc",
        "session_id": None,
        "message": "用一句话说明1+1等于几",
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/api/chat/stream",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    event_counts: dict[str, int] = {}
    reasoning_chars = 0
    content_chars = 0
    current_event = ""
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if line.startswith("event:"):
                current_event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            event = current_event or str(data.get("event", ""))
            event_counts[event] = event_counts.get(event, 0) + 1
            if event == "messages":
                reasoning_chars += len(data.get("reasoning") or "")
                content_chars += len(data.get("content") or "")
    print("event counts:", json.dumps(event_counts, ensure_ascii=False))
    print(f"messages content chars: {content_chars}")
    print(f"messages reasoning chars: {reasoning_chars}")
    print(f"token_usage events: {event_counts.get('token_usage', 0)}")


if __name__ == "__main__":
    main()
