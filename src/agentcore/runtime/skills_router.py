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
* ``POST   /api/skills/pool/{skill_name}/install/{agent_id}`` — install to agent
* ``GET    /api/skills/agents/{agent_id}``               — list installed skills
* ``DELETE /api/skills/agents/{agent_id}/{skill_name}``  — uninstall a skill
"""

from __future__ import annotations

import logging
import re
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agentcore.runtime import paths

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])

# Skill names double as directory names — keep them strictly safe.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


# ---------------------------------------------------------------------------
# Skill pool seeding (called once at startup)
# ---------------------------------------------------------------------------

SKILL_POOL_SAMPLES: dict[str, str] = {
    "translator": """---
name: translator
description: 中英文互译技能，保持原文语气与格式，适用于用户请求翻译时
---

# 翻译技能

## 何时使用

- 用户要求将一段文字翻译成其他语言时。

## 执行步骤

1. 识别源语言与目标语言（未指明时默认中英互译）。
2. 保持原文的语气、格式与专有名词拼写。
3. 只输出译文，不附加解释；如用户要求对照，再提供双语对照。
""",
    "weekly-report": """---
name: weekly-report
description: 生成周报的技能，汇总本周完成事项、下周计划与风险项
---

# 周报生成技能

## 何时使用

- 用户要求写周报、工作总结或进度汇报时。

## 输出结构

1. **本周完成** — 按优先级列出已完成事项。
2. **进行中** — 尚未完成但有进展的事项。
3. **下周计划** — 明确可验证的计划项。
4. **风险与求助** — 阻塞点及需要的支持。

## 注意事项

- 每条内容一句话，可量化处尽量给出数字。
""",
}


def ensure_skill_pool() -> None:
    """Create the skill pool directory and seed sample skills.

    Idempotent: existing skills (user-edited or not) are never touched.
    Only runs on a completely empty pool.
    """
    pool_dir = paths.get_skill_pool_dir()
    try:
        pool_dir.mkdir(parents=True, exist_ok=True)
        if any(pool_dir.iterdir()):
            return
        for name, content in SKILL_POOL_SAMPLES.items():
            skill_dir = pool_dir / name
            skill_dir.mkdir(exist_ok=True)
            (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        logger.info("Skill pool seeded with %d sample skill(s)", len(SKILL_POOL_SAMPLES))
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
    return {
        "name": meta.get("name") or skill_dir.name,
        "dir_name": skill_dir.name,
        "description": meta.get("description", ""),
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


async def _resolve_workspace(request: Request, agent_id: str) -> Any:
    """Return the workspace for *agent_id*, lazily loading it when needed."""
    manager = getattr(request.app.state, "agent_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="MultiAgentManager is not available.")

    workspace = manager.get_workspace(agent_id)
    if workspace is not None:
        return workspace

    store = getattr(request.app.state, "store", None)
    known = store is not None and agent_id in getattr(store, "agent_states", {})
    if not known:
        raise HTTPException(status_code=404, detail="Agent not found")

    return await manager.get_or_create_workspace(agent_id)


def _build_skill_md(name: str, description: str, content: str) -> str:
    """Compose a SKILL.md from name/description + markdown body."""
    body = content.strip()
    heading = f"# {name}"
    if not body:
        body = f"{heading}\n\n{description}\n"
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


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
# Routes — install / per-agent skills
# ---------------------------------------------------------------------------


@router.post("/pool/{skill_name}/install/{agent_id}")
async def install_skill(skill_name: str, agent_id: str, request: Request) -> dict[str, Any]:
    """Install a pool skill into an agent (copy to workspace ``skills/``)."""
    _validate_skill_name(skill_name)
    source = paths.get_skill_pool_dir() / skill_name
    if not (source / "SKILL.md").exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 不存在")

    workspace = await _resolve_workspace(request, agent_id)
    target = workspace.workspace_dir / "skills" / skill_name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    logger.info("Skill %s installed to agent %s", skill_name, agent_id)
    return {"result": "ok", "skill_name": skill_name, "agent_id": agent_id}


@router.get("/agents/{agent_id}")
async def list_agent_skills(agent_id: str, request: Request) -> dict[str, Any]:
    """List the skills installed on an agent."""
    workspace = await _resolve_workspace(request, agent_id)
    skills_dir = workspace.workspace_dir / "skills"
    return {"agent_id": agent_id, "skills": _list_skills(skills_dir)}


@router.delete("/agents/{agent_id}/{skill_name}")
async def uninstall_skill(agent_id: str, skill_name: str, request: Request) -> dict[str, Any]:
    """Uninstall a skill from an agent (removes its workspace copy)."""
    _validate_skill_name(skill_name)
    workspace = await _resolve_workspace(request, agent_id)
    skill_dir = workspace.workspace_dir / "skills" / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 {skill_name!r} 未安装")
    shutil.rmtree(skill_dir)
    logger.info("Skill %s uninstalled from agent %s", skill_name, agent_id)
    return {"result": "ok"}
