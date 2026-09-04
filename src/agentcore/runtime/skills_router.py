"""Global skill pool management router.

The skill pool (``.agentcore/skill_pool/``) is a shared repository of
Agent Skills (directories containing a ``SKILL.md``).  Skills can be
created, inspected and deleted in the pool, and *installed* into an
agent by copying them into the agent's workspace ``skills/`` directory
(where the deepagents ``SkillsMiddleware`` picks them up).

Endpoints
---------
* ``GET    /api/skills/pool``                            — list pool skills
* ``POST   /api/skills/pool``                            — create a skill
* ``GET    /api/skills/pool/{skill_name}``               — read skill content
* ``PUT    /api/skills/pool/{skill_name}``               — update skill content
* ``DELETE /api/skills/pool/{skill_name}``               — delete a skill
* ``GET    /api/skills/pool/{skill_name}/files``         — list files in skill dir
* ``GET    /api/skills/pool/{skill_name}/files/content`` — read a file in skill
* ``PUT    /api/skills/pool/{skill_name}/files/content`` — write a file in skill
* ``POST   /api/skills/pool/{skill_name}/files/upload``  — upload file(s) / zip
* ``DELETE /api/skills/pool/{skill_name}/files``         — delete file in skill
* ``POST   /api/skills/pool/upload-zip``                — bulk import skills from zip
* ``POST   /api/skills/pool/{skill_name}/install/{agent_id}`` — install to agent
  (body may carry ``agent_ids`` to install to several agents at once)
* ``POST   /api/skills/pool/{skill_name}/sync``          — push pool content
  to every agent that already has the skill installed
* ``GET    /api/skills/agents/{agent_id}``               — list installed skills
* ``GET    /api/skills/agents/{agent_id}/{skill_name}``  — read installed skill content
* ``PUT    /api/skills/agents/{agent_id}/{skill_name}``  — enable/disable a skill
* ``DELETE /api/skills/agents/{agent_id}/{skill_name}``  — uninstall a skill
* ``GET    /api/skills/agents/{agent_id}/{skill_name}/files``         — list files
* ``GET    /api/skills/agents/{agent_id}/{skill_name}/files/content`` — read file
* ``PUT    /api/skills/agents/{agent_id}/{skill_name}/files/content`` — write file
* ``POST   /api/skills/agents/{agent_id}/{skill_name}/files/upload``  — upload/zip
* ``DELETE /api/skills/agents/{agent_id}/{skill_name}/files``         — delete file
"""

from __future__ import annotations

import asyncio
import io
import logging
import posixpath
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from agentcore.runtime import paths
from agentcore.runtime.agent_ids import known_agent_ids

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])

# Skill names double as directory names — keep them strictly safe.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


# ---------------------------------------------------------------------------
# Skill pool seeding (called once at startup)
# ---------------------------------------------------------------------------

# Built-in sample skills live as plain files next to this module under
# ``runtime/pool_samples/<skill-name>/`` (``SKILL.md`` + optional reference
# files).  Keeping them as real skill directories — instead of Python string
# literals — keeps them editor-friendly, supports multi-file skills, and lets
# seeding copy them verbatim (``pyproject.toml`` ships ``runtime/**`` as
# package data).

_POOL_SAMPLES_DIR = Path(__file__).resolve().parent / "pool_samples"


def _iter_pool_sample_dirs() -> list[Path]:
    """Return built-in sample skill directories (must contain a SKILL.md)."""
    if not _POOL_SAMPLES_DIR.is_dir():
        return []
    return sorted(
        d
        for d in _POOL_SAMPLES_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists() and _SKILL_NAME_RE.match(d.name)
    )


def ensure_skill_pool() -> None:
    """Create the skill pool directory and seed sample skills.

    Idempotent: existing skills (user-edited or not) are never touched.
    Seeding and upgrade-migration are the same operation — any sample
    skill missing from the pool is copied in as a whole directory tree
    (SKILL.md plus any accompanying files).
    """
    pool_dir = paths.get_skill_pool_dir()
    try:
        pool_dir.mkdir(parents=True, exist_ok=True)
        existing = {d.name for d in pool_dir.iterdir() if d.is_dir()}
        added: list[str] = []
        for sample_dir in _iter_pool_sample_dirs():
            if sample_dir.name in existing:
                continue
            shutil.copytree(sample_dir, pool_dir / sample_dir.name)
            added.append(sample_dir.name)
        if added:
            logger.info(
                "Skill pool seeded with %d sample skill(s): %s",
                len(added), ", ".join(added),
            )
    except OSError as exc:
        logger.warning("Failed to seed skill pool: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_skill_name(skill_name: str) -> str:
    if not _SKILL_NAME_RE.match(skill_name):
        raise HTTPException(
            status_code=400,
            detail="技能名只能包含小写字母、数字和连字符（最长 64，须以字母或数字开头）",
        )
    return skill_name


def _parse_frontmatter(content: str) -> dict[str, str]:
    """Extract ``name`` / ``description`` from a SKILL.md YAML frontmatter."""
    meta: dict[str, str] = {}
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return meta
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() in ("name", "description"):
            meta[key.strip()] = value.strip().strip("'\"")
    return meta


def _skill_summary(skill_dir: Any) -> dict[str, Any] | None:
    """Build a summary dict for one skill directory (or ``None``)."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None
    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    meta = _parse_frontmatter(content)
    # Count files (recursive, excluding directories)
    try:
        file_count = sum(1 for _ in skill_dir.rglob("*") if _.is_file())
    except OSError:
        file_count = 1
    return {
        "name": meta.get("name") or skill_dir.name,
        "dir_name": skill_dir.name,
        "description": meta.get("description", ""),
        "file_count": file_count,
    }


def _list_skills(root: Any) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    skills: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        summary = _skill_summary(entry)
        if summary is not None:
            skills.append(summary)
    return skills


def _disabled_skills(workspace: Any) -> set[str]:
    """Return the set of disabled skill names from ``agent.json``."""
    try:
        config = workspace.read_agent_config()
    except Exception:
        logger.exception("Failed to read agent config for skills state")
        return set()
    disabled = (config.get("skills", {}) or {}).get("disabled", []) or []
    return {name for name in disabled if isinstance(name, str)}


def _set_skill_enabled(workspace: Any, skill_name: str, enabled: bool) -> None:
    """Persist the enabled/disabled state of an installed skill."""
    config = workspace.read_agent_config()
    disabled = set((config.get("skills", {}) or {}).get("disabled", []) or [])
    if enabled:
        disabled.discard(skill_name)
    else:
        disabled.add(skill_name)
    config.setdefault("skills", {})["disabled"] = sorted(disabled)
    workspace.write_agent_config(config)


def _install_skill_into_workspace(
    skill_name: str, source: Any, workspace: Any
) -> None:
    """Copy a pool skill into a workspace and (re-)enable it."""
    target = workspace.workspace_dir / "skills" / skill_name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    # Installing implies enabling: a reinstall must never stay disabled.
    _set_skill_enabled(workspace, skill_name, True)


async def _resolve_workspace(request: Request, agent_id: str) -> Any:
    """Return the workspace for *agent_id*, lazily loading it when needed."""
    manager = getattr(request.app.state, "agent_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="MultiAgentManager is not available.")

    workspace = manager.get_workspace(agent_id)
    if workspace is not None:
        return workspace

    # The agent may be known (persisted state, runtime tracking or an
    # on-disk workspace directory) but its workspace not loaded yet.
    if agent_id not in known_agent_ids(request.app.state):
        raise HTTPException(status_code=404, detail="Agent not found")

    return await manager.get_or_create_workspace(agent_id)


async def _invalidate_graph(request: Request, agent_id: str) -> None:
    """Best-effort agent graph invalidation after a skill change."""
    from agentcore.runtime.chat_router import invalidate_agent_graph

    try:
        await invalidate_agent_graph(request.app.state, agent_id)
    except Exception:
        logger.exception(
            "Failed to invalidate agent graph for %s after skill change",
            agent_id,
        )


def _build_skill_md(name: str, description: str, content: str) -> str:
    """Compose a SKILL.md from name/description + markdown body."""
    body = content.strip()
    heading = f"# {name}"
    if not body:
        body = f"{heading}\n\n{description}\n"
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


# ---------------------------------------------------------------------------
# Skill file management helpers
# ---------------------------------------------------------------------------

MAX_SKILL_FILE_SIZE = 2 * 1024 * 1024  # 2 MiB cap for in-browser editing


class SkillFileContentUpdate(BaseModel):
    """Payload for overwriting a file inside a skill directory."""
    content: str


def _resolve_within_skill(skill_dir: Path, rel_path: str) -> Path:
    """Resolve *rel_path* against *skill_dir*, blocking traversal.

    Returns the resolved absolute :class:`~pathlib.Path`.  Raises 403
    when the resolved path escapes the skill directory.
    """
    skill_dir = skill_dir.resolve()
    normalised = posixpath.normpath(rel_path.replace("\\", "/")).lstrip("/")
    if not normalised or normalised == ".":
        return skill_dir
    target = (skill_dir / normalised).resolve()
    if target != skill_dir and skill_dir not in target.parents:
        raise HTTPException(status_code=403, detail="Path traversal not allowed")
    return target


def _list_skill_dir_sync(skill_dir: Path) -> list[dict[str, Any]]:
    """List one skill directory's entries (directories first)."""
    items: list[dict[str, Any]] = []
    for item in sorted(skill_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            size = item.stat().st_size if item.is_file() else None
        except OSError:
            size = None
        items.append({
            "name": item.name,
            "path": item.relative_to(skill_dir).as_posix(),
            "is_dir": item.is_dir(),
            "size": size,
        })
    return items


def _list_skill_dir_recursive(skill_dir: Path) -> list[dict[str, Any]]:
    """Recursively list all files in a skill directory."""
    items: list[dict[str, Any]] = []
    for item in sorted(skill_dir.rglob("*"), key=lambda p: (not p.is_dir(), p.as_posix().lower())):
        try:
            size = item.stat().st_size if item.is_file() else None
        except OSError:
            size = None
        items.append({
            "name": item.name,
            "path": item.relative_to(skill_dir).as_posix(),
            "is_dir": item.is_dir(),
            "size": size,
        })
    return items


def _extract_zip_to_skill(zip_bytes: bytes, skill_dir: Path) -> list[str]:
    """Extract a zip archive into a skill directory.

    Returns the list of extracted relative file paths.  Entries that
    would escape the skill directory are silently skipped.
    """
    extracted: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            # Skip directories and hidden/system entries
            if info.is_dir():
                continue
            # Normalise the entry name (strip common root like "my-skill/")
            entry_name = info.filename.replace("\\", "/")
            # Strip a single leading directory component if all entries
            # share one (common when zipping a folder).
            # We'll handle this after detecting the common prefix.
            target = (skill_dir / entry_name).resolve()
            if target != skill_dir.resolve() and skill_dir.resolve() not in target.parents:
                continue
            # Create parent dirs and write the file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            extracted.append(target.relative_to(skill_dir.resolve()).as_posix())
    return extracted


def _strip_common_prefix(names: list[str]) -> list[str]:
    """If all *names* share a single leading directory, strip it."""
    if not names:
        return names
    prefixes = [n.split("/")[0] for n in names if "/" in n]
    if not prefixes:
        return names
    common = prefixes[0]
    if all(p == common for p in prefixes) and all(n.startswith(common + "/") for n in names):
        return [n[len(common) + 1:] for n in names]
    return names


def _sanitize_skill_dir_name(name: str) -> str:
    """Sanitize a folder name into a valid skill directory name.

    Converts to lowercase, replaces invalid characters with hyphens,
    strips leading/trailing hyphens, and validates against the skill
    name pattern.  Returns an empty string when the result is invalid.
    """
    sanitized = re.sub(r"[^a-z0-9-]", "-", name.strip().lower())
    sanitized = sanitized.strip("-")
    # Collapse consecutive hyphens
    sanitized = re.sub(r"-{2,}", "-", sanitized)
    if not sanitized or not _SKILL_NAME_RE.match(sanitized):
        return ""
    return sanitized[:64]


# ---------------------------------------------------------------------------
# Routes — skill pool
# ---------------------------------------------------------------------------


@router.get("/pool")
async def list_skill_pool() -> dict[str, Any]:
    """List all skills in the global skill pool."""
    ensure_skill_pool()
    pool_dir = paths.get_skill_pool_dir()
    return {"skills": _list_skills(pool_dir)}


@router.post("/pool")
async def create_skill(body: dict) -> dict[str, Any]:
    """Create a new skill in the pool.

    Body: ``{"name": ..., "description": ..., "content": "..."}``.
    ``content`` is the markdown body (frontmatter is generated).
    """
    name = (body.get("name") or "").strip().lower()
    _validate_skill_name(name)
    description = (body.get("description") or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="缺少技能描述 description")

    pool_dir = paths.get_skill_pool_dir()
    skill_dir = pool_dir / name
    if skill_dir.exists():
        raise HTTPException(status_code=409, detail=f"技能 {name!r} 已存在")

    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        _build_skill_md(name, description, body.get("content") or ""),
        encoding="utf-8",
    )
    logger.info("Skill %s created in pool", name)
    return {"result": "ok", "skill": _skill_summary(skill_dir)}


@router.get("/pool/{skill_name}")
async def read_skill(skill_name: str) -> dict[str, Any]:
    """Read one pool skill's metadata and full SKILL.md content."""
    _validate_skill_name(skill_name)
    skill_md = paths.get_skill_pool_dir() / skill_name / "SKILL.md"
    if not skill_md.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 不存在")
    content = skill_md.read_text(encoding="utf-8")
    summary = _skill_summary(skill_md.parent) or {}
    return {**summary, "content": content}


@router.put("/pool/{skill_name}")
async def update_skill(skill_name: str, body: dict) -> dict[str, Any]:
    """Update a pool skill's description and/or content."""
    _validate_skill_name(skill_name)
    skill_dir = paths.get_skill_pool_dir() / skill_name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 不存在")

    existing = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    description = (body.get("description") or existing.get("description", "")).strip()
    content = body.get("content")
    if content is None:
        # Keep the existing markdown body (strip the old frontmatter).
        raw = skill_md.read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\n.*?\n---\s*\n", raw, re.DOTALL)
        content = raw[match.end():] if match else raw

    skill_md.write_text(_build_skill_md(skill_name, description, content), encoding="utf-8")
    logger.info("Skill %s updated in pool", skill_name)
    return {"result": "ok", "skill": _skill_summary(skill_dir)}


@router.post("/pool/upload-zip")
async def upload_zip_to_pool(
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload a ZIP archive containing one or more skill directories.

    The ZIP should contain folder(s) with a ``SKILL.md`` inside.
    Each such folder is imported as a skill into the pool.
    Supports nested subdirectories within each skill.

    Examples of valid ZIP layouts::

        # Single skill
        my-skill/
            SKILL.md
            assets/icon.png

        # Multiple skills
        skill-a/
            SKILL.md
        skill-b/
            SKILL.md
            templates/
                prompt.txt

    Returns a summary of imported skills.
    """
    filename = (file.filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if not filename or not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 .zip 格式的压缩包")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="压缩包内容为空")

    def _import() -> dict[str, Any]:
        pool_dir = paths.get_skill_pool_dir()
        pool_dir.mkdir(parents=True, exist_ok=True)
        existing_skills = {
            d.name for d in pool_dir.iterdir() if d.is_dir()
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Extract zip
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                zf.extractall(tmp_path)

            # Collect all top-level entries
            top_entries = [p for p in tmp_path.iterdir()]

            # Find skill directories (those containing SKILL.md)
            skill_dirs: list[tuple[str, Path]] = []  # (dir_name, dir_path)
            for entry in top_entries:
                if entry.is_dir() and (entry / "SKILL.md").exists():
                    skill_dirs.append((entry.name, entry))

            imported: list[dict[str, str]] = []
            skipped: list[str] = []

            if len(skill_dirs) >= 1:
                # Case 1 & 2: one or more top-level skill directories
                for dir_name, dir_path in skill_dirs:
                    sanitized = _sanitize_skill_dir_name(dir_name)
                    if not sanitized:
                        skipped.append(dir_name)
                        continue
                    target = pool_dir / sanitized
                    if sanitized in existing_skills:
                        skipped.append(sanitized)
                        continue
                    shutil.copytree(dir_path, target)
                    summary = _skill_summary(target)
                    imported.append(summary or {
                        "name": sanitized, "dir_name": sanitized,
                    })
            else:
                # Case 3: files at root or nested — treat as single skill
                # Find the deepest SKILL.md
                skill_mds = list(tmp_path.rglob("SKILL.md"))
                if not skill_mds:
                    raise HTTPException(
                        status_code=400,
                        detail="压缩包中未找到包含 SKILL.md 的技能目录",
                    )
                # Use the skill folder name from the SKILL.md path
                skill_md = skill_mds[0]
                skill_root = skill_md.parent
                dir_name = skill_root.name
                # If SKILL.md is at tmp root, derive name from zip filename
                if skill_root == tmp_path:
                    dir_name = filename.rsplit(".", 1)[0]
                sanitized = _sanitize_skill_dir_name(dir_name)
                if not sanitized:
                    raise HTTPException(
                        status_code=400,
                        detail="无法从压缩包中推断有效的技能名",
                    )
                target = pool_dir / sanitized
                if sanitized in existing_skills:
                    skipped.append(sanitized)
                else:
                    shutil.copytree(skill_root, target)
                    summary = _skill_summary(target)
                    imported.append(summary or {
                        "name": sanitized, "dir_name": sanitized,
                    })

        return {
            "imported": imported,
            "imported_count": len(imported),
            "skipped": skipped,
            "skipped_count": len(skipped),
        }

    return await asyncio.to_thread(_import)


@router.delete("/pool/{skill_name}")
async def delete_skill(skill_name: str) -> dict[str, Any]:
    """Delete a skill from the pool."""
    _validate_skill_name(skill_name)
    skill_dir = paths.get_skill_pool_dir() / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 不存在")
    shutil.rmtree(skill_dir)
    logger.info("Skill %s deleted from pool", skill_name)
    return {"result": "ok"}


# ---------------------------------------------------------------------------
# Routes — skill pool file management
# ---------------------------------------------------------------------------


@router.get("/pool/{skill_name}/files")
async def list_pool_skill_files(skill_name: str) -> dict[str, Any]:
    """List all files in a pool skill directory (recursive)."""
    _validate_skill_name(skill_name)
    skill_dir = paths.get_skill_pool_dir() / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 不存在")
    items = await asyncio.to_thread(_list_skill_dir_recursive, skill_dir)
    return {"skill_name": skill_name, "files": items}


@router.get("/pool/{skill_name}/files/content")
async def read_pool_skill_file(skill_name: str, path: str) -> dict[str, Any]:
    """Read a file from within a pool skill directory."""
    _validate_skill_name(skill_name)
    skill_dir = paths.get_skill_pool_dir() / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 不存在")
    target = _resolve_within_skill(skill_dir, path)

    def _read() -> str:
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail=f"File {path!r} not found")
        if target.stat().st_size > MAX_SKILL_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File too large to edit in browser")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="Binary file not supported") from exc

    content = await asyncio.to_thread(_read)
    return {"path": path, "content": content}


@router.put("/pool/{skill_name}/files/content")
async def write_pool_skill_file(
    skill_name: str, path: str, payload: SkillFileContentUpdate
) -> dict[str, Any]:
    """Write (create or overwrite) a file inside a pool skill directory."""
    _validate_skill_name(skill_name)
    skill_dir = paths.get_skill_pool_dir() / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 不存在")
    target = _resolve_within_skill(skill_dir, path)

    def _write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload.content, encoding="utf-8")

    await asyncio.to_thread(_write)
    logger.info("Skill pool[%s]: wrote %s (%d chars)", skill_name, path, len(payload.content))
    return {"path": path, "size": len(payload.content.encode("utf-8"))}


@router.post("/pool/{skill_name}/files/upload")
async def upload_pool_skill_files(
    skill_name: str,
    request: Request,
    file: UploadFile = File(...),
    path: str = "",
) -> dict[str, Any]:
    """Upload file(s) or a zip archive into a pool skill directory.

    When the uploaded file is a ``.zip`` archive it is extracted into
    the skill directory (preserving subdirectory structure).  Otherwise
    the file is stored as-is under *path*.
    """
    _validate_skill_name(skill_name)
    skill_dir = paths.get_skill_pool_dir() / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 不存在")

    filename = (file.filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if not filename or filename in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid file name")

    content = await file.read()

    # Zip extraction
    if filename.lower().endswith(".zip"):
        def _extract() -> list[str]:
            # Detect and strip common prefix if the zip wraps a single folder
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = [n.replace("\\", "/") for n in zf.namelist() if not n.endswith("/")]
            stripped = _strip_common_prefix(names)
            # Re-extract with prefix stripping
            extracted: list[str] = []
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for info in zf.infolist():
                    entry = info.filename.replace("\\", "/")
                    if info.is_dir():
                        continue
                    # Apply prefix stripping
                    orig_names = [n.replace("\\", "/") for n in zf.namelist() if not n.endswith("/")]
                    stripped_map = dict(zip(orig_names, stripped))
                    rel = stripped_map.get(entry, entry)
                    if path:
                        rel = f"{path}/{rel}"
                    target = _resolve_within_skill(skill_dir, rel)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(info))
                    extracted.append(target.relative_to(skill_dir.resolve()).as_posix())
            return extracted

        extracted = await asyncio.to_thread(_extract)
        logger.info("Skill pool[%s]: extracted zip %s (%d files)", skill_name, filename, len(extracted))
        return {"extracted": extracted, "count": len(extracted)}

    # Regular file upload
    target_dir = _resolve_within_skill(skill_dir, path)
    target_path = target_dir / filename
    # Verify containment
    _resolve_within_skill(skill_dir, target_path.relative_to(skill_dir.resolve()).as_posix())

    def _write() -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)

    await asyncio.to_thread(_write)
    rel = target_path.relative_to(skill_dir.resolve()).as_posix()
    logger.info("Skill pool[%s]: uploaded %s (%d bytes)", skill_name, filename, len(content))
    return {"path": rel, "size": len(content)}


@router.delete("/pool/{skill_name}/files")
async def delete_pool_skill_file(skill_name: str, path: str, recursive: bool = False) -> dict[str, Any]:
    """Delete a file or subdirectory inside a pool skill directory."""
    _validate_skill_name(skill_name)
    skill_dir = paths.get_skill_pool_dir() / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 不存在")
    if not path or path.strip() in ("", ".", "/"):
        raise HTTPException(status_code=400, detail="Path is required")
    target = _resolve_within_skill(skill_dir, path)
    if target == skill_dir.resolve():
        raise HTTPException(status_code=400, detail="Cannot delete the skill root directory")

    def _delete() -> str:
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Path {path!r} not found")
        if target.is_dir():
            if not recursive:
                raise HTTPException(status_code=400, detail="Directories require recursive=true")
            shutil.rmtree(target)
            return "directory"
        target.unlink()
        return "file"

    kind = await asyncio.to_thread(_delete)
    logger.info("Skill pool[%s]: deleted %s %s", skill_name, kind, path)
    return {"path": path, "kind": kind}


# ---------------------------------------------------------------------------
# Routes — install / per-agent skills
# ---------------------------------------------------------------------------


@router.post("/pool/{skill_name}/install/{agent_id}")
async def install_skill(
    skill_name: str, agent_id: str, request: Request, body: dict | None = None
) -> dict[str, Any]:
    """Install a pool skill into an agent (copy to workspace ``skills/``).

    The optional body ``{"agent_ids": [...]}`` extends the installation
    to additional agents in one call (the path agent is always included).
    """
    _validate_skill_name(skill_name)
    source = paths.get_skill_pool_dir() / skill_name
    if not (source / "SKILL.md").exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 不存在")

    agent_ids = [agent_id]
    if body:
        extra = body.get("agent_ids") or []
        if isinstance(extra, list):
            agent_ids.extend(str(a) for a in extra if a and str(a) != agent_id)

    installed: list[str] = []
    for target_agent_id in agent_ids:
        workspace = await _resolve_workspace(request, target_agent_id)
        _install_skill_into_workspace(skill_name, source, workspace)
        await _invalidate_graph(request, target_agent_id)
        installed.append(target_agent_id)
    logger.info("Skill %s installed to agent(s) %s", skill_name, installed)
    return {"result": "ok", "skill_name": skill_name, "agent_ids": installed}


@router.post("/pool/{skill_name}/sync")
async def sync_skill(skill_name: str, request: Request) -> dict[str, Any]:
    """Push the pool version of a skill to every agent that installed it.

    Pool edits do not propagate to workspace copies automatically; this
    endpoint overwrites each installed copy with the current pool content
    (enable/disable state is preserved) and invalidates the affected
    agent graphs so the next chat picks up the change.
    """
    _validate_skill_name(skill_name)
    source = paths.get_skill_pool_dir() / skill_name
    if not (source / "SKILL.md").exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 不存在")

    manager = getattr(request.app.state, "agent_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="MultiAgentManager is not available.")

    synced: list[str] = []
    for agent_id, workspace in manager.get_loaded_workspaces().items():
        target = workspace.workspace_dir / "skills" / skill_name
        if not target.exists():
            continue
        shutil.rmtree(target)
        shutil.copytree(source, target)
        await _invalidate_graph(request, agent_id)
        synced.append(agent_id)
    logger.info("Skill %s synced to agent(s) %s", skill_name, synced or "none")
    return {"result": "ok", "skill_name": skill_name, "synced_agents": synced}


@router.get("/agents/{agent_id}")
async def list_agent_skills(agent_id: str, request: Request) -> dict[str, Any]:
    """List the skills installed on an agent (with enabled state)."""
    workspace = await _resolve_workspace(request, agent_id)
    skills_dir = workspace.workspace_dir / "skills"
    disabled = _disabled_skills(workspace)
    skills = [
        {**summary, "enabled": summary["dir_name"] not in disabled}
        for summary in _list_skills(skills_dir)
    ]
    return {"agent_id": agent_id, "skills": skills}


@router.get("/agents/{agent_id}/{skill_name}")
async def read_agent_skill(
    agent_id: str, skill_name: str, request: Request
) -> dict[str, Any]:
    """Read one installed skill's metadata and full SKILL.md content."""
    _validate_skill_name(skill_name)
    workspace = await _resolve_workspace(request, agent_id)
    skill_dir = workspace.workspace_dir / "skills" / skill_name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 未安装")
    content = skill_md.read_text(encoding="utf-8")
    summary = _skill_summary(skill_dir) or {}
    disabled = _disabled_skills(workspace)
    return {
        **summary,
        "enabled": skill_name not in disabled,
        "content": content,
    }


@router.put("/agents/{agent_id}/{skill_name}")
async def toggle_skill(
    agent_id: str, skill_name: str, request: Request, body: dict
) -> dict[str, Any]:
    """Enable or disable an installed skill for the agent.

    Body: ``{"enabled": true | false}``.  The skill stays installed; a
    disabled skill is simply filtered out of the agent's skill index.
    """
    _validate_skill_name(skill_name)
    workspace = await _resolve_workspace(request, agent_id)
    skill_dir = workspace.workspace_dir / "skills" / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 未安装")

    enabled = bool(body.get("enabled", True))
    _set_skill_enabled(workspace, skill_name, enabled)

    # Drop the cached agent graph (and checkpointed skills_metadata) so
    # the next chat rebuilds the agent with the updated skill index.
    await _invalidate_graph(request, agent_id)

    logger.info(
        "Skill %s %s for agent %s",
        skill_name,
        "enabled" if enabled else "disabled",
        agent_id,
    )
    return {"result": "ok", "skill": skill_name, "enabled": enabled}


@router.delete("/agents/{agent_id}/{skill_name}")
async def uninstall_skill(agent_id: str, skill_name: str, request: Request) -> dict[str, Any]:
    """Uninstall a skill from an agent (removes its workspace copy)."""
    _validate_skill_name(skill_name)
    workspace = await _resolve_workspace(request, agent_id)
    skill_dir = workspace.workspace_dir / "skills" / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 未安装")
    shutil.rmtree(skill_dir)
    # Clean up any stale disable entry so a later reinstall starts enabled.
    _set_skill_enabled(workspace, skill_name, True)
    await _invalidate_graph(request, agent_id)
    logger.info("Skill %s uninstalled from agent %s", skill_name, agent_id)
    return {"result": "ok"}


# ---------------------------------------------------------------------------
# Routes — per-agent skill file management
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/{skill_name}/files")
async def list_agent_skill_files(
    agent_id: str, skill_name: str, request: Request
) -> dict[str, Any]:
    """List all files in an agent's installed skill directory (recursive)."""
    _validate_skill_name(skill_name)
    workspace = await _resolve_workspace(request, agent_id)
    skill_dir = workspace.workspace_dir / "skills" / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 未安装")
    items = await asyncio.to_thread(_list_skill_dir_recursive, skill_dir)
    return {"skill_name": skill_name, "files": items}


@router.get("/agents/{agent_id}/{skill_name}/files/content")
async def read_agent_skill_file(
    agent_id: str, skill_name: str, path: str, request: Request
) -> dict[str, Any]:
    """Read a file from within an agent's installed skill directory."""
    _validate_skill_name(skill_name)
    workspace = await _resolve_workspace(request, agent_id)
    skill_dir = workspace.workspace_dir / "skills" / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 未安装")
    target = _resolve_within_skill(skill_dir, path)

    def _read() -> str:
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail=f"File {path!r} not found")
        if target.stat().st_size > MAX_SKILL_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File too large to edit in browser")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="Binary file not supported") from exc

    content = await asyncio.to_thread(_read)
    return {"path": path, "content": content}


@router.put("/agents/{agent_id}/{skill_name}/files/content")
async def write_agent_skill_file(
    agent_id: str,
    skill_name: str,
    path: str,
    request: Request,
    payload: SkillFileContentUpdate,
) -> dict[str, Any]:
    """Write (create or overwrite) a file inside an agent's installed skill."""
    _validate_skill_name(skill_name)
    workspace = await _resolve_workspace(request, agent_id)
    skill_dir = workspace.workspace_dir / "skills" / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 未安装")
    target = _resolve_within_skill(skill_dir, path)

    def _write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload.content, encoding="utf-8")

    await asyncio.to_thread(_write)
    await _invalidate_graph(request, agent_id)
    logger.info("Skill[%s/%s]: wrote %s (%d chars)", agent_id, skill_name, path, len(payload.content))
    return {"path": path, "size": len(payload.content.encode("utf-8"))}


@router.post("/agents/{agent_id}/{skill_name}/files/upload")
async def upload_agent_skill_files(
    agent_id: str,
    skill_name: str,
    request: Request,
    file: UploadFile = File(...),
    path: str = "",
) -> dict[str, Any]:
    """Upload file(s) or a zip archive into an agent's installed skill."""
    _validate_skill_name(skill_name)
    workspace = await _resolve_workspace(request, agent_id)
    skill_dir = workspace.workspace_dir / "skills" / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 未安装")

    filename = (file.filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if not filename or filename in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid file name")

    content = await file.read()

    if filename.lower().endswith(".zip"):
        def _extract() -> list[str]:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = [n.replace("\\", "/") for n in zf.namelist() if not n.endswith("/")]
            stripped = _strip_common_prefix(names)
            extracted: list[str] = []
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for info in zf.infolist():
                    entry = info.filename.replace("\\", "/")
                    if info.is_dir():
                        continue
                    orig_names = [n.replace("\\", "/") for n in zf.namelist() if not n.endswith("/")]
                    stripped_map = dict(zip(orig_names, stripped))
                    rel = stripped_map.get(entry, entry)
                    if path:
                        rel = f"{path}/{rel}"
                    target = _resolve_within_skill(skill_dir, rel)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(info))
                    extracted.append(target.relative_to(skill_dir.resolve()).as_posix())
            return extracted

        extracted = await asyncio.to_thread(_extract)
        await _invalidate_graph(request, agent_id)
        logger.info("Skill[%s/%s]: extracted zip %s (%d files)", agent_id, skill_name, filename, len(extracted))
        return {"extracted": extracted, "count": len(extracted)}

    target_dir = _resolve_within_skill(skill_dir, path)
    target_path = target_dir / filename
    _resolve_within_skill(skill_dir, target_path.relative_to(skill_dir.resolve()).as_posix())

    def _write() -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)

    await asyncio.to_thread(_write)
    await _invalidate_graph(request, agent_id)
    rel = target_path.relative_to(skill_dir.resolve()).as_posix()
    logger.info("Skill[%s/%s]: uploaded %s (%d bytes)", agent_id, skill_name, filename, len(content))
    return {"path": rel, "size": len(content)}


@router.delete("/agents/{agent_id}/{skill_name}/files")
async def delete_agent_skill_file(
    agent_id: str, skill_name: str, path: str, request: Request, recursive: bool = False
) -> dict[str, Any]:
    """Delete a file or subdirectory inside an agent's installed skill."""
    _validate_skill_name(skill_name)
    workspace = await _resolve_workspace(request, agent_id)
    skill_dir = workspace.workspace_dir / "skills" / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 未安装")
    if not path or path.strip() in ("", ".", "/"):
        raise HTTPException(status_code=400, detail="Path is required")
    target = _resolve_within_skill(skill_dir, path)
    if target == skill_dir.resolve():
        raise HTTPException(status_code=400, detail="Cannot delete the skill root directory")

    def _delete() -> str:
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Path {path!r} not found")
        if target.is_dir():
            if not recursive:
                raise HTTPException(status_code=400, detail="Directories require recursive=true")
            shutil.rmtree(target)
            return "directory"
        target.unlink()
        return "file"

    kind = await asyncio.to_thread(_delete)
    await _invalidate_graph(request, agent_id)
    logger.info("Skill[%s/%s]: deleted %s %s", agent_id, skill_name, kind, path)
    return {"path": path, "kind": kind}
