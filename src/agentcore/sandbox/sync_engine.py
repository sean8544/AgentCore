"""Bidirectional file sync between a workspace and its AIO sandbox.

The whole agent workspace mirrors the sandbox container's home directory
(``/home/gem`` in the AIO image): skills, memory, kernel files and user
data all travel together.  Sync is hash-based and incremental:

* **push** — upload locally new/modified files to the container.
* **pull** — recursively scan the container root and download files that
  are new or modified remotely (e.g. produced by the agent).
* **manifest** — ``.sync_manifest.json`` at the workspace root records
  the sha256 of every file at last sync time so three-way diffs can
  detect which side changed.

Internal files are excluded from the mirror: hidden (dot) files,
``agent.json`` (AgentCore configuration) and cache directories such as
``node_modules`` / ``__pycache__``.

Conflict handling is conservative: when both sides changed a file since
the last sync, the remote version is stored locally as
``<name>.remote-conflict`` and the entry is flagged ``conflict`` —
nothing is overwritten silently.

The engine is backend-agnostic: it only uses the async primitives of
``SandboxBackendProtocol`` (``aexecute`` / ``als`` / ``aupload_files`` /
``adownload_files``), so it works with both the AIO and OpenSandbox
backends.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shlex
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Manifest file name (workspace root; hidden from the files tree UI).
MANIFEST_NAME = ".sync_manifest.json"

# Default container-side root (AIO image default WORKSPACE = /home/gem).
DEFAULT_CONTAINER_ROOT = "/home/gem"

# Files larger than this are skipped (reported in the sync report).
DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024

# Directory names never synced in either direction.
SKIP_DIRS = {"Downloads", ".ipynb_checkpoints", "__pycache__", "node_modules"}

# Workspace-internal files never mirrored (AgentCore configuration).
EXCLUDED_FILES = {"agent.json"}

# Sync states
SYNCED = "synced"
LOCAL_MODIFIED = "local_modified"
REMOTE_MODIFIED = "remote_modified"
CONFLICT = "conflict"
LOCAL_ONLY = "local_only"
REMOTE_ONLY = "remote_only"

_MANIFEST_VERSION = 1


async def wait_backend_ready(backend: Any, timeout: float = 90.0) -> bool:
    """Wait until the sandbox backend answers a cheap probe command.

    A freshly created container returns 502 for a few seconds while its
    all-in-one server boots.  Returns ``True`` once ``pwd`` succeeds.
    """
    import asyncio
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = await backend.aexecute("pwd", timeout=10)
            if getattr(resp, "exit_code", -1) == 0:
                return True
        except Exception:
            pass
        await asyncio.sleep(3)
    return False


class SandboxSyncEngine:
    """Hash-based bidirectional sync between workspace and sandbox."""

    def __init__(
        self,
        workspace_dir: Path | str,
        *,
        container_root: str = DEFAULT_CONTAINER_ROOT,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.local_root = self.workspace_dir
        self.container_root = container_root.rstrip("/") or "/"
        self.max_file_bytes = max_file_bytes
        self._manifest_path = self.workspace_dir / MANIFEST_NAME
        self._manifest = self._load_manifest()
        self._container_id: str | None = None  # Set via note_container_id()

    def note_container_id(self, container_id: str) -> None:
        """Record the current container id and reset the manifest when it
        changes.

        The manifest tracks which files have already been uploaded to a
        specific container.  When the container is recreated (system
        restart, sandbox death, manual destroy) the filesystem is empty
        but the manifest still says "all files synced" — so push() skips
        every upload and the agent runs with no workspace files.

        By tying the manifest to the container id we force a full
        re-upload whenever the container changes, while keeping the
        incremental optimisation for the same container (resume, idle
        wake-up, etc.).
        """
        if self._container_id is not None and self._container_id == container_id:
            return  # Same container — no reset needed
        prev = self._manifest.get("container_id")
        if prev is not None and prev != container_id:
            logger.info(
                "Sync engine: container changed from %s to %s — "
                "resetting manifest for full re-upload",
                prev, container_id,
            )
            self._manifest["files"] = {}
            self._manifest.pop("states", None)
            self._manifest["last_sync_at"] = None
        self._manifest["container_id"] = container_id
        self._container_id = container_id
        self._save_manifest()

    # -- configuration ------------------------------------------------------

    @classmethod
    def from_agent_config(
        cls, workspace_dir: Path | str, agent_config: dict[str, Any]
    ) -> "SandboxSyncEngine | None":
        """Build an engine when the agent uses a sandbox backend.

        Returns ``None`` for non-sandbox agents or when sync is disabled
        via ``settings.backend.file_sync.enabled = false``.
        """
        backend_cfg = (agent_config.get("settings") or {}).get("backend", {}) or {}
        if backend_cfg.get("type") != "sandbox":
            return None
        file_sync = backend_cfg.get("file_sync", {}) or {}
        if file_sync.get("enabled", True) is False:
            return None
        return cls(
            workspace_dir,
            container_root=str(file_sync.get("container_root", DEFAULT_CONTAINER_ROOT)),
            max_file_bytes=int(
                file_sync.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)
            ),
        )

    # -- manifest ------------------------------------------------------------

    def reset_manifest(self) -> None:
        """Clear the sync manifest to force a full re-upload on next push.

        Use this when the sync state is inconsistent (e.g. container was
        manually modified, manifest corrupted, or user wants to guarantee
        all files are re-uploaded).  The container_id is preserved so the
        next push still knows which container it's talking to.
        """
        container_id = self._manifest.get("container_id")
        self._manifest = {
            "version": _MANIFEST_VERSION,
            "container_id": container_id,
            "last_sync_at": None,
            "files": {},
        }
        self._save_manifest()
        logger.info(
            "Sync manifest reset for %s (container_id=%s)",
            self.workspace_dir, container_id,
        )

    def _load_manifest(self) -> dict[str, Any]:
        try:
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": _MANIFEST_VERSION, "last_sync_at": None, "files": {}}
        if not isinstance(data, dict):
            return {"version": _MANIFEST_VERSION, "last_sync_at": None, "files": {}}
        data.setdefault("files", {})
        data.setdefault("last_sync_at", None)
        return data

    def _save_manifest(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_text(
            json.dumps(self._manifest, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    def cached_states(self) -> dict[str, str]:
        """States recorded by the last status/sync run (for cheap tree UI)."""
        return dict(self._manifest.get("states", {}) or {})

    # -- local scan ----------------------------------------------------------

    @staticmethod
    def is_excluded(rel: str) -> bool:
        """True when a workspace-relative path is excluded from sync."""
        parts = rel.split("/")
        if any(p.startswith(".") for p in parts):
            return True
        if any(p in SKIP_DIRS for p in parts):
            return True
        return rel in EXCLUDED_FILES

    def scan_local(self) -> dict[str, dict[str, Any]]:
        """Return ``{rel_path: {"sha256", "size"}}`` for the workspace."""
        files: dict[str, dict[str, Any]] = {}
        if not self.local_root.exists():
            return files
        for item in self.local_root.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(self.local_root).as_posix()
            if self.is_excluded(rel):
                continue
            try:
                stat = item.stat()
                sha = self._sha256_file(item)
            except OSError:
                logger.warning("sync: cannot hash local file %s", rel)
                continue
            files[rel] = {"sha256": sha, "size": stat.st_size}
        return files

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    # -- remote scan ---------------------------------------------------------

    async def scan_remote(self, backend: Any) -> dict[str, dict[str, Any]]:
        """Return ``{rel_path: {"sha256", "size"}}`` for the container root.

        Sizes come from a single recursive listing; hashes from one
        batched ``sha256sum`` execution.  Unreadable files fall back to
        size-only comparison (``sha256=None``).
        """
        sizes: dict[str, int] = {}
        await self._collect_remote_files(backend, self.container_root, "", sizes)

        files: dict[str, dict[str, Any]] = {
            rel: {"sha256": None, "size": size} for rel, size in sizes.items()
        }
        if files:
            hashes = await self._remote_hashes(backend, list(files))
            files.update(hashes)
        return files

    async def _collect_remote_files(
        self, backend: Any, abs_dir: str, rel_prefix: str, out: dict[str, int]
    ) -> None:
        result = await backend.als(abs_dir)
        if getattr(result, "error", None):
            if "no such file" in str(result.error).lower():
                return  # missing dir == empty
            raise RuntimeError(f"sandbox listing failed: {result.error}")
        for entry in getattr(result, "entries", []) or []:
            name = entry["path"].rsplit("/", 1)[-1]
            if name.startswith(".") or name in SKIP_DIRS or name in EXCLUDED_FILES:
                continue
            rel = f"{rel_prefix}{name}" if not rel_prefix else f"{rel_prefix}/{name}"
            if rel_prefix == "":
                rel = name
            if entry["is_dir"]:
                await self._collect_remote_files(
                    backend, f"{abs_dir}/{name}", rel, out
                )
            else:
                out[rel] = int(entry.get("size") or 0)

    async def _remote_hashes(
        self, backend: Any, rel_paths: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Batched sha256 via one shell command.  Robust fallback: size-only."""
        quoted = " ".join(
            shlex.quote(f"{self.container_root}/{p}") for p in rel_paths
        )
        cmd = (
            f"sha256sum {quoted} 2>/dev/null || "
            f"shasum -a 256 {quoted} 2>/dev/null || true"
        )
        resp = await backend.aexecute(cmd)
        hashes: dict[str, dict[str, Any]] = {}
        if getattr(resp, "exit_code", -1) == 0 or getattr(resp, "output", ""):
            by_abs: dict[str, str] = {}
            for line in (resp.output or "").splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) != 2 or len(parts[0]) != 64:
                    continue
                by_abs[parts[1].strip().lstrip("*")] = parts[0]
            prefix = self.container_root + "/"
            for rel in rel_paths:
                sha = by_abs.get(f"{prefix}{rel}")
                if sha:
                    hashes[rel] = {"sha256": sha}
        return hashes

    # -- status ---------------------------------------------------------------

    async def status(self, backend: Any | None) -> dict[str, Any]:
        """Compute per-file sync states.

        When *backend* is ``None`` (container not running), states degrade
        to local-vs-manifest (``synced`` / ``local_modified`` /
        ``local_only``) so the UI can still show pending pushes.
        """
        from datetime import datetime, timezone

        local = self.scan_local()
        manifest_files: dict[str, Any] = self._manifest.get("files", {}) or {}

        remote: dict[str, dict[str, Any]] = {}
        if backend is not None:
            remote = await self.scan_remote(backend)

        states: dict[str, str] = {}
        for rel, info in local.items():
            entry = manifest_files.get(rel) or {}
            m_sha = entry.get("sha256")
            if rel not in remote:
                # File exists locally but NOT in the container — it must
                # be pushed.  The manifest hash is irrelevant here: even
                # if the file was previously synced, the container no
                # longer has it (recreated, manually deleted, etc.).
                states[rel] = LOCAL_ONLY
                continue
            r_sha = remote[rel].get("sha256")
            if r_sha is None:
                # Hash unavailable remotely — fall back to size comparison.
                same = remote[rel].get("size") == info["size"]
                states[rel] = SYNCED if same else LOCAL_MODIFIED
                continue
            if info["sha256"] == r_sha:
                states[rel] = SYNCED
            elif m_sha == info["sha256"]:
                states[rel] = REMOTE_MODIFIED
            elif m_sha == r_sha:
                states[rel] = LOCAL_MODIFIED
            else:
                states[rel] = CONFLICT
        for rel in remote:
            if rel not in local:
                entry = manifest_files.get(rel)
                states[rel] = LOCAL_ONLY if entry else REMOTE_ONLY

        self._manifest["states"] = states
        self._manifest["last_status_at"] = datetime.now(timezone.utc).isoformat()
        self._save_manifest()
        return {
            "container_root": self.container_root,
            "last_sync_at": self._manifest.get("last_sync_at"),
            "states": states,
        }

    # -- push ------------------------------------------------------------------

    async def push(self, backend: Any) -> dict[str, Any]:
        """Upload locally new/modified files to the container.

        Before deciding which files to skip, the method verifies each
        "unchanged" file actually exists in the container.  The manifest
        hash alone is not trustworthy: the container may have been
        recreated (empty filesystem) while the manifest still records the
        old hashes, causing every file to be silently skipped.
        """
        from datetime import datetime, timezone

        local = self.scan_local()
        manifest_files: dict[str, Any] = self._manifest.get("files", {}) or {}
        pushed: list[str] = []
        skipped: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []

        # -- Determine which manifest-"unchanged" files are truly present -
        # Collect files the manifest claims are already synced.
        manifest_unchanged: list[str] = []
        for rel, info in local.items():
            entry = manifest_files.get(rel) or {}
            if entry.get("sha256") == info["sha256"]:
                manifest_unchanged.append(rel)

        # Verify those files actually exist in the container.
        missing_from_remote: set[str] = set()
        if manifest_unchanged:
            remote_sizes: dict[str, int] = {}
            await self._collect_remote_files(
                backend, self.container_root, "", remote_sizes
            )
            for rel in manifest_unchanged:
                if rel not in remote_sizes:
                    missing_from_remote.add(rel)
            if missing_from_remote:
                logger.warning(
                    "sync push: manifest claims %d file(s) synced but they are "
                    "missing from the container — re-uploading",
                    len(missing_from_remote),
                )

        to_upload: list[tuple[str, bytes]] = []
        for rel, info in sorted(local.items()):
            entry = manifest_files.get(rel) or {}
            if (
                entry.get("sha256") == info["sha256"]
                and rel not in missing_from_remote
            ):
                skipped.append({"path": rel, "reason": "unchanged"})
                continue
            if info["size"] > self.max_file_bytes:
                skipped.append({"path": rel, "reason": "too_large"})
                continue
            try:
                content = (self.local_root / rel).read_bytes()
            except OSError as exc:
                errors.append({"path": rel, "error": str(exc)})
                continue
            to_upload.append((f"{self.container_root}/{rel}", content))

        if to_upload:
            results = await backend.aupload_files(to_upload)
            uploaded_root_len = len(self.container_root) + 1
            for res in results:
                rel = res.path[uploaded_root_len:] if res.path.startswith(
                    self.container_root + "/"
                ) else res.path
                if res.error:
                    errors.append({"path": rel, "error": res.error})
                else:
                    pushed.append(rel)
                    self._manifest["files"][rel] = {
                        "sha256": local[rel]["sha256"],
                        "size": local[rel]["size"],
                    }

        if pushed or not errors:
            self._manifest["last_sync_at"] = datetime.now(timezone.utc).isoformat()
        self._manifest.pop("states", None)
        self._save_manifest()
        logger.info(
            "sync push: %d uploaded, %d skipped, %d errors",
            len(pushed), len(skipped), len(errors),
        )
        return {
            "direction": "push",
            "pushed": pushed,
            "pulled": [],
            "skipped": skipped,
            "errors": errors,
            "conflicts": [],
        }

    # -- pull ------------------------------------------------------------------

    async def pull(self, backend: Any) -> dict[str, Any]:
        """Download remotely new/modified files from the container."""
        from datetime import datetime, timezone

        local = self.scan_local()
        remote = await self.scan_remote(backend)
        manifest_files: dict[str, Any] = self._manifest.get("files", {}) or {}
        pulled: list[str] = []
        skipped: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        conflicts: list[str] = []

        to_fetch: list[str] = []
        fetch_target: dict[str, str] = {}  # rel -> local relative path
        for rel, r_info in sorted(remote.items()):
            l_info = local.get(rel)
            entry = manifest_files.get(rel) or {}
            m_sha = entry.get("sha256")
            r_sha = r_info.get("sha256")

            if l_info is not None and r_sha is not None and l_info["sha256"] == r_sha:
                continue  # identical content
            if l_info is None and m_sha is None:
                pass  # brand-new remote file
            elif r_sha is not None and m_sha == r_sha:
                continue  # remote unchanged since last sync
            elif l_info is not None and m_sha != l_info["sha256"] and (
                r_sha is None or m_sha != r_sha
            ):
                # Both sides changed — conflict: keep local, save remote copy.
                conflicts.append(rel)
                fetch_target[rel] = f"{rel}.remote-conflict"
            if r_info.get("size", 0) > self.max_file_bytes:
                skipped.append({"path": rel, "reason": "too_large"})
                continue
            to_fetch.append(f"{self.container_root}/{rel}")

        if to_fetch:
            results = await backend.adownload_files(to_fetch)
            prefix_len = len(self.container_root) + 1
            for res in results:
                rel = res.path[prefix_len:] if res.path.startswith(
                    self.container_root + "/"
                ) else res.path
                if res.error or res.content is None:
                    errors.append({"path": rel, "error": str(res.error)})
                    continue
                target_rel = fetch_target.get(rel, rel)
                target = self.local_root / target_rel
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(res.content)
                except OSError as exc:
                    errors.append({"path": rel, "error": str(exc)})
                    continue
                pulled.append(target_rel)
                # Record the synced hash only for non-conflict paths.
                if target_rel == rel:
                    r_sha = remote[rel].get("sha256")
                    self._manifest["files"][rel] = {
                        "sha256": r_sha or self._sha256_file(target),
                        "size": len(res.content),
                    }
                else:
                    self._manifest["files"][rel] = {
                        "sha256": remote[rel].get("sha256"),
                        "size": len(res.content),
                        "conflict": True,
                    }

        if pulled or not errors:
            self._manifest["last_sync_at"] = datetime.now(timezone.utc).isoformat()
        self._manifest.pop("states", None)
        self._save_manifest()
        logger.info(
            "sync pull: %d downloaded, %d skipped, %d conflicts, %d errors",
            len(pulled), len(skipped), len(conflicts), len(errors),
        )
        return {
            "direction": "pull",
            "pushed": [],
            "pulled": pulled,
            "skipped": skipped,
            "errors": errors,
            "conflicts": conflicts,
        }
