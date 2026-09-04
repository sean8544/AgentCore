"""Workspace → Sandbox file synchronisation.

Pushes kernel files, memory, and skills from the agent's local
workspace directory into the sandbox container so the agent has
everything it needs when it starts executing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Files/directories to sync from the workspace into the sandbox.
_SEED_PATTERNS = [
    # Kernel files
    "bootstrap.md",
    "agent.md",
    "AGENTS.md",
    # Memory
    "memory/",
    # Skills
    "skills/",
]


async def seed_sandbox(
    backend: Any,
    workspace_dir: Path,
    *,
    extra_files: list[tuple[str, str | bytes]] | None = None,
) -> None:
    """Push seed files from the workspace into the sandbox.

    Parameters
    ----------
    backend : Any
        A sandbox backend instance (must support ``write_file``).
    workspace_dir : Path
        The agent's local workspace directory.
    extra_files : list[tuple[str, str | bytes]] | None
        Additional ``(sandbox_path, content)`` pairs to write.
    """
    if not workspace_dir.is_dir():
        logger.warning(
            "Workspace directory %s does not exist — skipping seed",
            workspace_dir,
        )
        return

    synced = 0

    for pattern in _SEED_PATTERNS:
        src = workspace_dir / pattern
        if pattern.endswith("/"):
            # Directory — walk recursively
            if src.is_dir():
                for file_path in src.rglob("*"):
                    if file_path.is_file():
                        rel = file_path.relative_to(workspace_dir)
                        sandbox_path = f"/workspace/{rel.as_posix()}"
                        await _write_to_sandbox(backend, sandbox_path, file_path)
                        synced += 1
        else:
            # Single file
            if src.is_file():
                sandbox_path = f"/workspace/{pattern}"
                await _write_to_sandbox(backend, sandbox_path, src)
                synced += 1

    # Extra files (e.g. environment hints)
    if extra_files:
        for sandbox_path, content in extra_files:
            try:
                if hasattr(backend, "write_file"):
                    await backend.write_file(sandbox_path, content)
                synced += 1
            except Exception as exc:
                logger.warning(
                    "Failed to write extra file %s to sandbox: %s",
                    sandbox_path, exc,
                )

    logger.info("Seeded %d files into sandbox from %s", synced, workspace_dir)


async def sync_workspace_to_sandbox(
    backend: Any,
    workspace_dir: Path,
) -> int:
    """Full workspace sync — push all files to the sandbox.

    Returns the number of files synced.
    """
    if not workspace_dir.is_dir():
        return 0

    synced = 0
    for file_path in workspace_dir.rglob("*"):
        if file_path.is_file():
            rel = file_path.relative_to(workspace_dir)
            sandbox_path = f"/workspace/{rel.as_posix()}"
            await _write_to_sandbox(backend, sandbox_path, file_path)
            synced += 1

    logger.info(
        "Synced %d files from %s to sandbox", synced, workspace_dir,
    )
    return synced


async def _write_to_sandbox(
    backend: Any,
    sandbox_path: str,
    local_path: Path,
) -> None:
    """Write a single local file into the sandbox."""
    try:
        content = local_path.read_bytes()
        if hasattr(backend, "write_file"):
            # Async backend adapter
            await backend.write_file(sandbox_path, content)
        else:
            logger.debug(
                "Backend does not support write_file — skipping %s",
                sandbox_path,
            )
    except Exception as exc:
        logger.warning(
            "Failed to write %s to sandbox: %s",
            sandbox_path, exc,
        )
