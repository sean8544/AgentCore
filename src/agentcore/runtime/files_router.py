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

All endpoints resolve the requested path and reject anything escaping the
agent's ``workspace_dir`` (path-traversal protection).  Writing a kernel
file (``bootstrap.md`` / ``agent.md`` / ``profile.md`` / ``soul.md``) goes
through
:meth:`Workspace.write_kernel_file` and invalidates the cached agent graph
so the next chat picks up the new memory content; writes under ``skills/``
also invalidate so the SDK's SkillsMiddleware rediscovers the skill set.

Every blocking filesystem operation runs via :func:`asyncio.to_thread` so
the event loop is never stalled by disk I/O.
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agentcore.runtime.chat_router import invalidate_agent_graph
from agentcore.runtime.workspace import KERNEL_FILE_NAMES

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
# Synchronous filesystem operations (run inside asyncio.to_thread)
# ---------------------------------------------------------------------------


def _list_dir_sync(workspace: Any, target_dir: Any) -> list[dict[str, Any]]:
    """List one directory's entries (directories first)."""
    items: list[dict[str, Any]] = []
    for item in sorted(target_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
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

    Kernel files (``bootstrap.md`` / ``agent.md`` / ``profile.md`` /
    ``soul.md``) are routed
    through :meth:`Workspace.write_kernel_file` and the cached agent graph
    is invalidated so the next chat uses the updated memory content.
    Files under ``skills/`` likewise trigger invalidation so the SDK's
    SkillsMiddleware re-scans the skill set.
    """
    workspace = await _get_workspace(request, agent_id)
    file_path = _resolve_within_workspace(workspace, path)
    rel_path = _relative_path(workspace, file_path)

    if rel_path in KERNEL_FILE_NAMES:
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
