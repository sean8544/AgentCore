"""Sandbox module — optional pluggable sandbox for AgentCore.

This module is independent from ``agentcore.runtime`` and communicates
with it only through the :class:`SandboxBackendFactory` interface defined
in ``agentcore.runtime.sandbox``.

Install with::

    pip install agentcore[sandbox]

Or configure environment variables::

    SANDBOX_ENABLED=true
    SANDBOX_DOMAIN=localhost:8080
"""

from __future__ import annotations
