"""Shared pytest fixtures for the AgentCore test suite."""

from __future__ import annotations

import json
from typing import Any

import pytest


def _default_security() -> dict[str, Any]:
    """Deep copy of the default (approval disabled) security settings."""
    return json.loads(
        json.dumps({"approval": {"enabled": False, "tools": []}})
    )


@pytest.fixture(autouse=True)
def _disable_global_approval_in_tests(request, monkeypatch):
    """Pin the global security settings to the default (approval off).

    The global approval config is *instance* state persisted in
    ``.agentcore/security.json`` (the user's real configuration).  Graph
    building merges those rules into every agent, so a real-world enabled
    config would leak into assertions that assume a default environment
    (e.g. subagent specs must not carry ``interrupt_on``).

    ``test_security_settings`` is excluded: it already isolates itself via
    ``monkeypatch.chdir(tmp_path)`` and exercises explicit PUTs.
    """
    if request.module.__name__.endswith("test_security_settings"):
        return
    from agentcore.runtime import security_router

    monkeypatch.setattr(
        security_router,
        "load_security_settings",
        _default_security,
    )
