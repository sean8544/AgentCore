"""JSON-file-backed repository for agent states and chat sessions.

The Profile governance artifacts (drafts, published versions, tool
policies, permission rules, interrupt rules, approvals, traces) have been
removed together with the Profile layer; the store now only persists agent
runtime state and session metadata.
"""

from __future__ import annotations

from pathlib import Path
import json
import os
import tempfile

from agentcore.runtime import paths


# ---------------------------------------------------------------------------
# Atomic file I/O helpers
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, data: object) -> None:
    """Write *data* as JSON to *path* atomically (temp-file + rename)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp", dir=str(path.parent), prefix=f".{path.stem}_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Windows: target must not exist for os.replace — it does replace, but
        # to be safe we unlink first if it exists.
        if path.exists():
            path.unlink()
        os.replace(tmp, str(path))
    except BaseException:
        # Clean up temp file on failure.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> object:
    """Read and parse a JSON file, returning *None* if the file is missing."""

    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# ControlPlaneStore — JSON-file-backed control-plane persistence
# ---------------------------------------------------------------------------


class ControlPlaneStore:
    """JSON-file-backed persistence for agents and chat sessions.

    Data is kept in memory for fast access and flushed to disk after every
    write operation.  On startup the store loads existing data from the JSON
    files in *data_dir*.
    """

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self._data_dir = (
            Path(data_dir)
            if data_dir is not None
            else paths.get_data_dir() / "data"
        )
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # In-memory state — populated from disk below.
        self.agent_states: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.delegations: dict[str, list[dict]] = {}

        self._load_all()

    # -- file paths ---------------------------------------------------------

    @property
    def _agents_path(self) -> Path:
        return self._data_dir / "agents.json"

    @property
    def _sessions_dir(self) -> Path:
        return self._data_dir / "sessions"

    @property
    def _sessions_index_path(self) -> Path:
        return self._sessions_dir / "sessions_index.json"

    @property
    def _delegations_path(self) -> Path:
        return self._data_dir / "delegations.json"

    # Legacy single-file layout (migrated transparently on load).
    @property
    def _legacy_sessions_path(self) -> Path:
        return self._data_dir / "sessions.json"

    @staticmethod
    def _session_file_name(session_id: str) -> str:
        """Map *session_id* to a safe per-session file name."""
        return "".join(
            c if (c.isalnum() or c in "-_") else "_" for c in session_id
        )

    # -- bulk load ----------------------------------------------------------

    def _load_all(self) -> None:
        """Load all data files into memory (called once during __init__)."""

        self._load_agents()
        self._load_sessions()
        self._load_delegations()

    def _load_delegations(self) -> None:
        """Load the cross-agent delegation log from disk."""
        raw = _read_json(self._delegations_path)
        if raw and isinstance(raw, dict):
            self.delegations.update(raw)

    def _save_delegations(self) -> None:
        """Persist the delegation log atomically."""
        _atomic_write_json(self._delegations_path, self.delegations)

    def append_delegation(self, agent_id: str, record: dict) -> None:
        """Append a delegation record for *agent_id* (the subagent).

        *record* carries the delegating parent, the task description and
        a timestamp so a subagent's workspace can surface who asked it to
        do what.
        """
        records = self.delegations.setdefault(agent_id, [])
        records.append(record)
        # Keep the log bounded (most recent 200 entries per agent).
        del records[:-200]
        self._save_delegations()

    def list_delegations(self, agent_id: str) -> list[dict]:
        """Return delegation records received by *agent_id* (newest last)."""
        return list(self.delegations.get(agent_id, []))

    def _load_agents(self) -> None:
        raw = _read_json(self._agents_path)
        if not raw or not isinstance(raw, dict):
            return
        self.agent_states.update(raw.get("agent_states", {}))

    def _load_sessions(self) -> None:
        """Load sessions: per-file layout first, legacy file as migration."""

        # 1) New per-session-file layout.
        index = _read_json(self._sessions_index_path)
        if isinstance(index, dict):
            for sid in index:
                path = self._sessions_dir / (
                    f"{self._session_file_name(sid)}.json"
                )
                data = _read_json(path)
                if isinstance(data, dict):
                    self.sessions[sid] = data

        # 2) Legacy single-file layout — migrate once.
        if self._legacy_sessions_path.exists():
            raw = _read_json(self._legacy_sessions_path)
            legacy = (
                raw.get("sessions", {}) if isinstance(raw, dict) else {}
            )
            for sid, data in legacy.items():
                if sid in self.sessions or not isinstance(data, dict):
                    continue
                self.sessions[sid] = data
                self._save_session_file(sid, data)
            self._save_sessions_index()
            try:
                self._legacy_sessions_path.unlink()
            except OSError:
                pass

    # -- session persistence (per-file layout) ------------------------------

    def _save_session_file(self, session_id: str, data: dict) -> None:
        path = self._sessions_dir / (
            f"{self._session_file_name(session_id)}.json"
        )
        _atomic_write_json(path, data)

    def _save_sessions_index(self) -> None:
        index = {
            sid: {
                "agent_id": data.get("agent_id", ""),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "message_count": len(data.get("messages", [])),
            }
            for sid, data in self.sessions.items()
        }
        _atomic_write_json(self._sessions_index_path, index)

    # -- agent runtime state --------------------------------------------------

    def _save_agents(self) -> None:
        data = {"agent_states": dict(self.agent_states)}
        _atomic_write_json(self._agents_path, data)

    def save_agent_state(self, agent_id: str, state: dict) -> None:
        """Persist an agent's runtime state."""

        self.agent_states[agent_id] = state
        self._save_agents()

    def load_agent_state(self, agent_id: str) -> dict | None:
        """Load a previously persisted agent runtime state."""

        return self.agent_states.get(agent_id)

    def delete_agent_state(self, agent_id: str) -> None:
        """Remove an agent's persisted state (no-op when absent)."""

        if self.agent_states.pop(agent_id, None) is not None:
            self._save_agents()

    # -- session storage --------------------------------------------------------

    def save_session(self, session_id: str, data: dict) -> None:
        """Persist a session record (only this session's file is rewritten)."""

        self.sessions[session_id] = data
        self._save_session_file(session_id, data)
        self._save_sessions_index()

    def load_session(self, session_id: str) -> dict | None:
        """Load a single session by id."""

        return self.sessions.get(session_id)

    def load_sessions(self) -> dict[str, dict]:
        """Return all persisted sessions."""

        return dict(self.sessions)

    def delete_session(self, session_id: str) -> None:
        """Remove a session from persistence."""

        self.sessions.pop(session_id, None)
        path = self._sessions_dir / (
            f"{self._session_file_name(session_id)}.json"
        )
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
        self._save_sessions_index()
