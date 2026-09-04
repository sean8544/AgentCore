"""Workspace file management router: browse / read / write agent workspace files.

Exposes the per-agent :class:`~agentcore.runtime.workspace.Workspace`
directory as an HTTP API under ``/api/agents/{agent_id}/files``.

Endpoints
---------
* ``GET  /api/agents/{agent_id}/files/tree``     — list a directory
* ``GET  /api/agents/{agent_id}/files/content``  — read a text file
* ``PUT  /api/agents/{agent_id}/files/content``  — write a text file
* ``POST /api/agents/{agent_id}/files/upload``   — upload a file
* ``GET  /api/agents/{agent_id}/files/download`` — download a file
* ``DELETE /api/agents/{agent_id}/files``        — delete a file or directory
* ``GET  /api/agents/{agent_id}/files/sync/status`` — sandbox sync states
* ``POST /api/agents/{agent_id}/files/sync``     — manual push / pull / both

All endpoints resolve the requested path and reject anything escaping the
agent's ``workspace_dir`` (path-traversal protection).  Writing a kernel
file (``bootstrap.md`` / ``agent.md`` / ``profile.md`` / ``soul.md``) goes
through
:meth:`Workspace.write_kernel_file` and invalidates the cached agent graph
so the next chat picks up the new memory content; writes under ``skills/``
also invalidate so the SDK's SkillsMiddleware rediscovers the skill set.
Kernel files can never be deleted; deletions under ``skills/`` likewise
invalidate the cached graph.

Every blocking filesystem operation runs via :func:`asyncio.to_thread` so
the event loop is never stalled by disk I/O.
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
import shutil
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agentcore.runtime.chat_router import invalidate_agent_graph
from agentcore.runtime.workspace import ALL_KERNEL_FILE_NAMES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents/{agent_id}/files", tags=["files"])

# Binary-ish files that should never be opened as text via /content.
MAX_TEXT_FILE_SIZE = 2 * 1024 * 1024  # 2 MiB cap for in-browser editing


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class FileContentUpdate(BaseModel):
    """Payload for overwriting a workspace file's content."""

    content: str


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


async def _get_workspace(request: Request, agent_id: str) -> Any:
    """Resolve the workspace for *agent_id*, lazily loading when needed.

    Mirrors the resolution used by the other per-agent routers: an agent
    that is known (persisted) but not yet loaded gets its workspace loaded
    on demand; unknown agents raise 404.
    """
    manager = getattr(request.app.state, "agent_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="MultiAgentManager is not available.",
        )
    workspace = manager.get_workspace(agent_id)
    if workspace is not None:
        return workspace

    store = getattr(request.app.state, "store", None)
    known = store is not None and agent_id in getattr(store, "agent_states", {})
    if not known:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    return await manager.get_or_create_workspace(agent_id)


def _resolve_within_workspace(workspace: Any, rel_path: str) -> Any:
    """Resolve *rel_path* against the workspace dir, blocking traversal.

    Returns the resolved absolute :class:`~pathlib.Path`.  Raises 403 when
    the resolved path escapes ``workspace.workspace_dir``.
    """
    workspace_dir = workspace.workspace_dir.resolve()
    # Normalise Windows-style separators and collapse any ".." segments
    # before joining, so the containment check below is authoritative.
    normalised = posixpath.normpath(rel_path.replace("\\", "/")).lstrip("/")
    target = (workspace_dir / normalised).resolve()
    if target != workspace_dir and workspace_dir not in target.parents:
        raise HTTPException(status_code=403, detail="Path traversal not allowed")
    return target


def _relative_path(workspace: Any, target: Any) -> str:
    """Render *target* as a posix-style path relative to the workspace dir."""
    return target.resolve().relative_to(workspace.workspace_dir.resolve()).as_posix()


# ---------------------------------------------------------------------------
# Sandbox file-sync helpers
# ---------------------------------------------------------------------------


async def _get_sandbox_session(request: Request, agent_id: str) -> Any:
    """Return the agent's live sandbox session, or ``None``."""
    mgr = getattr(request.app.state, "sandbox_session_manager", None)
    if mgr is None:
        return None
    return mgr.get_session(agent_id)


async def _resolve_sync_context(request: Request, agent_id: str) -> dict[str, Any]:
    """Resolve the sync engine + live session for *agent_id*.

    Returns a dict with ``engine`` / ``session`` / ``backend`` keys;
    ``engine`` is ``None`` when the agent has no sandbox backend (or sync
    is disabled), in which case callers answer with ``enabled: false``.
    """
    from agentcore.sandbox.sync_engine import SandboxSyncEngine

    workspace = await _get_workspace(request, agent_id)
    try:
        agent_config = workspace.read_agent_config()
    except Exception:
        agent_config = {}
    engine = SandboxSyncEngine.from_agent_config(
        workspace.workspace_dir, agent_config
    )
    session = await _get_sandbox_session(request, agent_id) if engine else None
    backend = getattr(session, "backend", None) if session is not None else None
    # Bind the current container id so the manifest resets when the
    # container is recreated (system restart, sandbox death, etc.).
    if engine is not None and backend is not None:
        container_id = getattr(backend, "id", None) or getattr(session, "sandbox_id", None)
        if container_id:
            engine.note_container_id(str(container_id))
    return {
        "engine": engine,
        "session": session,
        "backend": backend,
        "workspace": workspace,
    }


def _sync_states_for_items(
    workspace: Any, items: list[dict[str, Any]]
) -> None:
    """Attach ``sync_state`` to workspace listing items.

    Uses the states cached by the last status/sync run (manifest), so a
    directory listing never talks to the container.
    """
    from agentcore.sandbox.sync_engine import MANIFEST_NAME, SandboxSyncEngine

    try:
        agent_config = workspace.read_agent_config()
    except Exception:
        return
    engine = SandboxSyncEngine.from_agent_config(
        workspace.workspace_dir, agent_config
    )
    if engine is None:
        return
    states = engine.cached_states()
    for item in items:
        path = item["path"]
        if path == MANIFEST_NAME or engine.is_excluded(path):
            continue  # internal metadata / config, not part of the mirror
        if item["is_dir"]:
            descendant = [
                s for k, s in states.items() if k.startswith(path + "/")
            ]
            if not descendant:
                item["sync_state"] = "local_only"
            else:
                item["sync_state"] = (
                    "mixed" if any(s != "synced" for s in descendant)
                    else "synced"
                )
        else:
            item["sync_state"] = states.get(path, "local_only")


# ---------------------------------------------------------------------------
# Synchronous filesystem operations (run inside asyncio.to_thread)
# ---------------------------------------------------------------------------


def _list_dir_sync(workspace: Any, target_dir: Any) -> list[dict[str, Any]]:
    """List one directory's entries (directories first)."""
    from agentcore.sandbox.sync_engine import MANIFEST_NAME

    items: list[dict[str, Any]] = []
    for item in sorted(target_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if item.name == MANIFEST_NAME:
            continue  # internal sync metadata, not part of the mirror
        try:
            size = item.stat().st_size if item.is_file() else None
        except OSError:
            size = None
        items.append(
            {
                "name": item.name,
                "path": _relative_path(workspace, item),
                "is_dir": item.is_dir(),
                "size": size,
            }
        )
    return items


def _read_text_sync(file_path: Any) -> str:
    """Read a text file, enforcing the in-browser editing size cap."""
    if file_path.stat().st_size > MAX_TEXT_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large to edit in browser")
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Binary file not supported") from exc


def _write_text_sync(file_path: Any, content: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def _write_bytes_sync(file_path: Any, content: bytes) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)


def _check_file_sync(file_path: Any) -> None:
    """Raise 404 unless *file_path* is an existing regular file."""
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File {file_path.name!r} not found")


def _delete_sync(target: Any, recursive: bool) -> str:
    """Delete a file, or a directory when *recursive* is set.

    Returns the deleted entry kind (``"file"`` / ``"directory"``).
    """
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path {target.name!r} not found")
    if target.is_dir():
        if not recursive:
            raise HTTPException(
                status_code=400, detail="Directories require recursive=true"
            )
        shutil.rmtree(target)
        return "directory"
    target.unlink()
    return "file"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/tree")
async def list_files(agent_id: str, request: Request, path: str = "") -> dict[str, Any]:
    """List the entries of a workspace directory (directories first)."""
    workspace = await _get_workspace(request, agent_id)
    target_dir = _resolve_within_workspace(workspace, path)

    def _inspect() -> list[dict[str, Any]]:
        if not target_dir.exists():
            raise HTTPException(status_code=404, detail=f"Path {path!r} not found")
        if not target_dir.is_dir():
            raise HTTPException(
                status_code=400, detail=f"Path {path!r} is not a directory"
            )
        return _list_dir_sync(workspace, target_dir)

    items = await asyncio.to_thread(_inspect)
    _sync_states_for_items(workspace, items)
    return {"path": _relative_path(workspace, target_dir) if path else "", "items": items}


@router.get("/content")
async def read_file(agent_id: str, request: Request, path: str) -> dict[str, Any]:
    """Read a text file from the workspace (UTF-8)."""
    workspace = await _get_workspace(request, agent_id)
    file_path = _resolve_within_workspace(workspace, path)

    def _read() -> str:
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail=f"File {path!r} not found")
        return _read_text_sync(file_path)

    content = await asyncio.to_thread(_read)
    return {"path": _relative_path(workspace, file_path), "content": content}


@router.put("/content")
async def write_file(
    agent_id: str, request: Request, path: str, payload: FileContentUpdate
) -> dict[str, Any]:
    """Write (create or overwrite) a text file in the workspace.

    Kernel files (``bootstrap.md`` / ``agent.md``, plus legacy
    ``profile.md`` / ``soul.md``) are routed
    through :meth:`Workspace.write_kernel_file` and the cached agent graph
    is invalidated so the next chat uses the updated memory content.
    Files under ``skills/`` likewise trigger invalidation so the SDK's
    SkillsMiddleware re-scans the skill set.
    """
    workspace = await _get_workspace(request, agent_id)
    file_path = _resolve_within_workspace(workspace, path)
    rel_path = _relative_path(workspace, file_path)

    if rel_path in ALL_KERNEL_FILE_NAMES:
        # Keeps the workspace's in-memory kernel cache consistent.
        await asyncio.to_thread(
            workspace.write_kernel_file, rel_path, payload.content
        )
        await invalidate_agent_graph(request.app.state, agent_id)
    else:
        try:
            await asyncio.to_thread(_write_text_sync, file_path, payload.content)
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Failed to write file: {exc}"
            ) from exc
        if rel_path.startswith("skills/"):
            await invalidate_agent_graph(request.app.state, agent_id)

    logger.info(
        "files[%s]: wrote %s (%d chars)", agent_id, rel_path, len(payload.content)
    )
    return {"path": rel_path, "size": len(payload.content.encode("utf-8"))}


@router.post("/upload")
async def upload_file(
    agent_id: str,
    request: Request,
    file: UploadFile = File(...),
    path: str = "",
) -> dict[str, Any]:
    """Upload a file into a workspace directory."""
    workspace = await _get_workspace(request, agent_id)

    filename = (file.filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if not filename or filename in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid file name")

    target_dir = _resolve_within_workspace(workspace, path)
    if target_dir.exists() and not target_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Path {path!r} is not a directory")

    target_path = _resolve_within_workspace(workspace, f"{path}/{filename}" if path else filename)

    content = await file.read()
    try:
        await asyncio.to_thread(_write_bytes_sync, target_path, content)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {exc}") from exc

    # Skill uploads change the agent's capability set — rebuild the graph
    # so the SDK's SkillsMiddleware picks up the new SKILL.md files.
    rel_path = _relative_path(workspace, target_path)
    if rel_path.startswith("skills/"):
        await invalidate_agent_graph(request.app.state, agent_id)

    logger.info("files[%s]: uploaded %s (%d bytes)", agent_id, filename, len(content))
    return {"path": rel_path, "size": len(content)}


@router.get("/download")
async def download_file(agent_id: str, request: Request, path: str) -> FileResponse:
    """Download a file from the workspace."""
    workspace = _get_workspace(request, agent_id)
    file_path = _resolve_within_workspace(workspace, path)

    try:
        await asyncio.to_thread(_check_file_sync, file_path)
    except HTTPException:
        raise HTTPException(status_code=404, detail=f"File {path!r} not found")

    return FileResponse(file_path, filename=file_path.name)


@router.delete("")
async def delete_file(
    agent_id: str, request: Request, path: str, recursive: bool = False
) -> dict[str, Any]:
    """Delete a workspace file, or a directory when ``recursive=true``.

    Kernel files (``bootstrap.md`` / ``agent.md`` / ``profile.md`` /
    ``soul.md``) are protected and always rejected with 403.  Deleting
    anything under ``skills/`` invalidates the cached agent graph so the
    SDK's SkillsMiddleware re-scans the remaining skill set.
    """
    workspace = await _get_workspace(request, agent_id)
    if not path or path.strip() in ("", ".", "/"):
        raise HTTPException(status_code=400, detail="Path is required")
    target = _resolve_within_workspace(workspace, path)
    rel_path = _relative_path(workspace, target)
    if rel_path in ALL_KERNEL_FILE_NAMES:
        raise HTTPException(status_code=403, detail="Kernel files cannot be deleted")

    try:
        kind = await asyncio.to_thread(_delete_sync, target, recursive)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to delete: {exc}"
        ) from exc

    # Skill deletions change the agent's capability set — rebuild the graph.
    if rel_path.startswith("skills/") or rel_path == "skills":
        await invalidate_agent_graph(request.app.state, agent_id)

    logger.info("files[%s]: deleted %s %s", agent_id, kind, rel_path)
    return {"path": rel_path, "kind": kind}


# ---------------------------------------------------------------------------
# Sandbox file-sync routes
# ---------------------------------------------------------------------------


@router.get("/sync/status")
async def sync_status(agent_id: str, request: Request) -> dict[str, Any]:
    """Report sandbox sync state for the agent's workspace mirror.

    When the sandbox container is not running, states degrade to the
    manifest-based view (local-only / local-modified) so the UI can still
    indicate pending pushes.
    """
    ctx = await _resolve_sync_context(request, agent_id)
    engine = ctx["engine"]
    if engine is None:
        return {"enabled": False}

    backend = ctx["backend"]
    session = ctx["session"]
    container_alive = session is not None and session.status == "running"

    if backend is not None:
        try:
            info = await engine.status(backend)
            states = info["states"]
            last_sync_at = info["last_sync_at"]
        except Exception as exc:
            logger.exception("sync status failed for %s", agent_id)
            return {
                "enabled": True,
                "sandbox_configured": True,
                "container_alive": False,
                "container_root": engine.container_root,
                "sandbox_id": getattr(session, "sandbox_id", None),
                "last_sync_at": None,
                "states": {},
                "error": str(exc),
            }
    else:
        states = engine.cached_states()
        last_sync_at = engine._manifest.get("last_sync_at")

    summary = {"synced": 0, "pending_push": 0, "pending_pull": 0, "conflict": 0}
    for state in states.values():
        if state == "synced":
            summary["synced"] += 1
        elif state in ("local_only", "local_modified"):
            summary["pending_push"] += 1
        elif state in ("remote_only", "remote_modified"):
            summary["pending_pull"] += 1
        elif state == "conflict":
            summary["conflict"] += 1

    return {
        "enabled": True,
        "sandbox_configured": True,
        "container_alive": container_alive,
        "container_root": engine.container_root,
        "sandbox_id": getattr(session, "sandbox_id", None),
        "last_sync_at": last_sync_at,
        "states": states,
        "summary": summary,
    }


async def _wait_sandbox_ready(backend: Any, timeout: float = 90.0) -> bool:
    """Wait until the AIO server inside the container answers."""
    from agentcore.sandbox.sync_engine import wait_backend_ready

    return await wait_backend_ready(backend, timeout=timeout)


class SyncRequest(BaseModel):
    """Payload for manual sandbox sync."""

    direction: str = "both"  # push | pull | both
    force: bool = False  # If True, reset manifest before sync for full re-upload


@router.post("/sync")
async def sync_files(
    agent_id: str, request: Request, payload: SyncRequest
) -> dict[str, Any]:
    """Manually sync the workspace with the sandbox container.

    The container is created on demand when it is not running.  Returns a
    report with pushed/pulled file lists, skips, conflicts and errors.

    When ``force`` is True, the sync manifest is cleared before the push
    so every workspace file is re-uploaded regardless of its cached hash.
    """
    direction = payload.direction
    if direction not in ("push", "pull", "both"):
        raise HTTPException(
            status_code=400, detail="direction must be push, pull or both"
        )

    ctx = await _resolve_sync_context(request, agent_id)
    engine = ctx["engine"]
    if engine is None:
        raise HTTPException(
            status_code=409,
            detail="Agent has no sandbox backend configured",
        )

    backend = ctx["backend"]
    if backend is None:
        # Start the container on demand.
        mgr = getattr(request.app.state, "sandbox_session_manager", None)
        workspace = ctx["workspace"]
        if mgr is None or workspace is None:
            raise HTTPException(
                status_code=409, detail="Sandbox module is not available"
            )
        try:
            agent_config = workspace.read_agent_config()
        except Exception:
            agent_config = {}
        backend_cfg = (agent_config.get("settings") or {}).get("backend", {})
        session = await mgr.get_or_create(agent_id, backend_cfg)
        backend = session.backend if session is not None else None
        if backend is None:
            raise HTTPException(
                status_code=503, detail="Failed to start the sandbox container"
            )
        # A freshly started container needs a few seconds before its
        # AIO server answers — wait instead of failing with 502.
        if not await _wait_sandbox_ready(backend):
            raise HTTPException(
                status_code=503,
                detail="Sandbox container started but is not ready yet",
            )
        # New container — reset manifest so push re-uploads everything.
        container_id = getattr(backend, "id", None) or session.sandbox_id
        if container_id:
            engine.note_container_id(str(container_id))

    report: dict[str, Any] = {
        "direction": direction,
        "pushed": [],
        "pulled": [],
        "skipped": [],
        "errors": [],
        "conflicts": [],
    }
    try:
        # Force full re-upload: clear manifest before push
        if payload.force and direction in ("push", "both"):
            engine.reset_manifest()
            report["force_reset"] = True
        if direction in ("push", "both"):
            r = await engine.push(backend)
            report["pushed"].extend(r["pushed"])
            report["skipped"].extend(r["skipped"])
            report["errors"].extend(r["errors"])
        if direction in ("pull", "both"):
            r = await engine.pull(backend)
            report["pulled"].extend(r["pulled"])
            report["skipped"].extend(r["skipped"])
            report["errors"].extend(r["errors"])
            report["conflicts"].extend(r["conflicts"])
        # Refresh cached states so the tree shows fresh badges.
        await engine.status(backend)
    except Exception as exc:
        logger.exception("sync failed for %s", agent_id)
        raise HTTPException(status_code=500, detail=f"Sync failed: {exc}") from exc

    logger.info(
        "files[%s]: sync %s — pushed=%d pulled=%d conflicts=%d errors=%d",
        agent_id,
        direction,
        len(report["pushed"]),
        len(report["pulled"]),
        len(report["conflicts"]),
        len(report["errors"]),
    )
    return report
