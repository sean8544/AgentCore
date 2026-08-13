"""Shared helper: resolve all agent ids known to the system.

Both :mod:`agentcore.runtime.agent_router` and
:mod:`agentcore.runtime.models_router` used to carry their own
(duplicate) ``_known_agent_ids`` implementation; this module hosts the
single consolidated version.

An agent id is considered known when it appears in any of:

* the loaded workspaces (:class:`MultiAgentManager`),
* the persisted agent states (:class:`ControlPlaneStore`),
* the runtime-tracked instances (:class:`AgentRuntime`),
* the on-disk workspace layout (``.agentcore/workspace/agent/*``).
"""

from __future__ import annotations

from typing import Any

from agentcore.runtime import paths


def known_agent_ids(state: Any) -> set[str]:
    """Return all agent ids known to the system (loaded, persisted, or tracked).

    Parameters
    ----------
    state:
        The owning application's ``app.state`` (any object exposing the
        optional ``agent_manager`` / ``store`` / ``runtime`` attributes).
    """
    ids: set[str] = set()

    manager = getattr(state, "agent_manager", None)
    if manager is not None:
        ids.update(manager.list_workspaces())

    store = getattr(state, "store", None)
    if store is not None:
        ids.update(getattr(store, "agent_states", {}).keys())

    runtime = getattr(state, "runtime", None)
    if runtime is not None:
        ids.update(inst.agent_id for inst in runtime.list_agents())

    # Fall back to the on-disk workspace layout.
    root = paths.get_workspace_root()
    if root.exists():
        try:
            ids.update(p.name for p in root.iterdir() if p.is_dir())
        except OSError:
            pass

    return ids
