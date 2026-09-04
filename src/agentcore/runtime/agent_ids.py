"""Shared helper: resolve the set of agent ids considered "alive".

Only ``store.agent_states`` (backed by ``agents.json``) is treated as
authoritative.  In-memory artefacts — loaded workspaces in
:class:`MultiAgentManager`, runtime-tracked instances in
:class:`AgentRuntime` — are deliberately **excluded**: a deleted agent can
still linger there (e.g. because a browser tab hit ``/api/agents/{id}/...``
before we finished the delete cleanup, which lazy-loaded its workspace
back).  Counting those as "known" was the primary zombie-resurrection
path: heartbeat / stats / sessions GET endpoints then transparently
recreated the on-disk workspace on the next beat, and the phantom agent
reappeared in the UI after a restart despite a successful purge.

Callers that legitimately need to include "an agent that is currently
being created and not yet persisted" (e.g. :class:`AgentService`) go
through the write path directly and never consult this helper.
"""

from __future__ import annotations

from typing import Any


def known_agent_ids(state: Any) -> set[str]:
    """Return the set of persisted agent ids (``agents.json`` view).

    Parameters
    ----------
    state:
        The owning application's ``app.state`` (any object exposing the
        optional ``store`` attribute).
    """
    store = getattr(state, "store", None)
    if store is None:
        return set()
    return set(getattr(store, "agent_states", {}).keys())
