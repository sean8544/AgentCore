"""Sandbox management REST API.

Optional — only mounted when the sandbox module is enabled
(``SANDBOX_ENABLED=true``).  Provides endpoints for monitoring and
managing sandbox sessions from the frontend dashboard.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])


def _get_session_manager(request: Request) -> Any:
    """Retrieve the SandboxSessionManager from app state."""
    mgr = getattr(request.app.state, "sandbox_session_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=503,
            detail="Sandbox module is not initialised. "
            "Set SANDBOX_ENABLED=true and restart.",
        )
    return mgr


@router.get("/status")
async def sandbox_status(request: Request) -> dict[str, Any]:
    """Global sandbox status — server reachability + session count."""
    mgr = _get_session_manager(request)
    return await mgr.health_check()


@router.get("/sessions")
async def list_sessions(request: Request) -> dict[str, Any]:
    """List all active sandbox sessions."""
    mgr = _get_session_manager(request)
    sessions = mgr.list_sessions()
    return {
        "sessions": [
            {
                "sandbox_id": s.sandbox_id,
                "agent_id": s.agent_id,
                "status": s.status,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "last_active_at": (
                    s.last_active_at.isoformat() if s.last_active_at else None
                ),
                "strategy": s.lifecycle_config.strategy,
                "timeout": s.lifecycle_config.timeout,
                "resume_count": s.resume_count,
            }
            for s in sessions
        ],
    }


@router.get("/sessions/{agent_id}")
async def get_session(agent_id: str, request: Request) -> dict[str, Any]:
    """Get details of a single sandbox session."""
    mgr = _get_session_manager(request)
    session = mgr.get_session(agent_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"No sandbox session for agent {agent_id}",
        )

    # Calculate timeout remaining
    timeout_remaining: int | None = None
    if session.status == "running" and session.lifecycle_config.timeout:
        from datetime import datetime, timezone
        elapsed = (datetime.now(timezone.utc) - session.last_active_at).total_seconds()
        timeout_remaining = max(0, int(session.lifecycle_config.timeout - elapsed))

    # Try to fetch live metrics from the sandbox
    cpu_usage: float | None = None
    memory_usage: int | None = None
    memory_limit: int | None = None
    if session.status == "running":
        try:
            from agentcore.sandbox.client import OpenSandboxClient

            client = OpenSandboxClient(mgr._global_config)
            metrics = await client.get_metrics(session.sandbox_id)
            if metrics.get("cpu_used_percentage") is not None:
                cpu_usage = float(metrics["cpu_used_percentage"])
            if metrics.get("memory_used_in_mib") is not None:
                memory_usage = int(float(metrics["memory_used_in_mib"]) * 1024 * 1024)
            if metrics.get("memory_total_in_mib") is not None:
                memory_limit = int(float(metrics["memory_total_in_mib"]) * 1024 * 1024)
        except Exception:
            logger.debug("Failed to fetch metrics for %s", session.sandbox_id)

    return {
        "sandbox_id": session.sandbox_id,
        "agent_id": session.agent_id,
        "status": session.status,
        "image": session.image or "—",
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "last_active_at": (
            session.last_active_at.isoformat() if session.last_active_at else None
        ),
        "strategy": session.lifecycle_config.strategy,
        "timeout": session.lifecycle_config.timeout,
        "idle_timeout": session.lifecycle_config.idle_timeout,
        "renew_on_chat": session.lifecycle_config.renew_on_chat,
        "renew_on_execute": session.lifecycle_config.renew_on_execute,
        "resume_count": session.resume_count,
        "timeout_remaining": timeout_remaining,
        "cpu_usage": cpu_usage,
        "memory_usage": memory_usage,
        "memory_limit": memory_limit,
    }


@router.delete("/sessions/{agent_id}")
async def destroy_session(agent_id: str, request: Request) -> dict[str, str]:
    """Manually destroy a sandbox session."""
    mgr = _get_session_manager(request)
    await mgr.destroy(agent_id)
    return {"status": "destroyed", "agent_id": agent_id}


@router.post("/sessions/{agent_id}/restart")
async def restart_session(
    agent_id: str, request: Request,
) -> dict[str, Any]:
    """Destroy and recreate a sandbox session."""
    mgr = _get_session_manager(request)
    session = mgr.get_session(agent_id)
    if session and session.backend_cfg:
        # Reuse the original backend config so image/provider survive —
        # a lifecycle-only dict would silently downgrade AIO agents to the
        # default python:3.12-slim image.
        backend_cfg: dict[str, Any] = dict(session.backend_cfg)
    elif session:
        backend_cfg = {"lifecycle": session.lifecycle_config.to_dict()}
    else:
        backend_cfg = {}
    new_session = await mgr.restart(agent_id, backend_cfg)
    return {
        "sandbox_id": new_session.sandbox_id,
        "agent_id": new_session.agent_id,
        "status": new_session.status,
    }


@router.post("/sessions/{agent_id}/renew")
async def renew_session(
    agent_id: str,
    request: Request,
    extra_seconds: int = 600,
) -> dict[str, str]:
    """Manually renew (extend) the sandbox timeout."""
    mgr = _get_session_manager(request)
    await mgr.renew(agent_id, extra_seconds=extra_seconds)
    return {"status": "renewed", "agent_id": agent_id}


@router.post("/sessions/{agent_id}/pause")
async def pause_session(
    agent_id: str, request: Request,
) -> dict[str, str]:
    """Pause a running sandbox (strategy B)."""
    mgr = _get_session_manager(request)
    await mgr.pause(agent_id)
    return {"status": "paused", "agent_id": agent_id}


@router.post("/sessions/{agent_id}/resume")
async def resume_session(
    agent_id: str, request: Request,
) -> dict[str, Any]:
    """Resume a paused sandbox."""
    mgr = _get_session_manager(request)
    session = await mgr.resume(agent_id)
    return {
        "sandbox_id": session.sandbox_id,
        "agent_id": session.agent_id,
        "status": session.status,
        "resume_count": session.resume_count,
    }


@router.post("/sessions/{agent_id}/sync")
async def sync_files(agent_id: str, request: Request) -> dict[str, Any]:
    """Push workspace files to the sandbox using the sync engine."""
    mgr = _get_session_manager(request)
    session = mgr.get_session(agent_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"No sandbox session for agent {agent_id}",
        )

    from agentcore.runtime import paths
    from agentcore.sandbox.sync_engine import SandboxSyncEngine, wait_backend_ready

    # Real layout is ``<data>/workspace/agent/<id>`` (paths is the single
    # source of truth; there is no app.state.workspace_root).
    workspace_dir = paths.get_agent_workspace_dir(agent_id)
    if not workspace_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Workspace directory not found for agent {agent_id}",
        )

    # Build sync engine from agent config
    try:
        manager = getattr(request.app.state, "agent_manager", None)
        workspace = manager.get_workspace(agent_id) if manager else None
        if workspace:
            agent_config = workspace.read_agent_config()
        else:
            agent_config = {}
    except Exception:
        agent_config = {}

    engine = SandboxSyncEngine.from_agent_config(workspace_dir, agent_config)
    if engine is None:
        # Fallback: use default container root
        engine = SandboxSyncEngine(workspace_dir)

    # Wait for backend to be ready
    ready = await wait_backend_ready(session.backend, timeout=60)
    if not ready:
        raise HTTPException(
            status_code=503,
            detail="Sandbox backend is not ready after 60s",
        )

    report = await engine.push(session.backend)
    return {
        "synced": len(report.get("pushed", [])),
        "pushed": report.get("pushed", []),
        "errors": report.get("errors", []),
        "agent_id": agent_id,
    }


# ===========================================================================
# Sandbox control plane (global) — thin proxy over the OpenSandbox server API
# ===========================================================================
#
# These endpoints deliberately do **not** depend on the per-agent
# ``sandbox_session_manager``: they build an :class:`OpenSandboxClient` straight
# from the effective config, so the connection can be configured / enabled /
# monitored even while the integration is currently disabled.  All container
# data comes from the OpenSandbox server (via the SDK); AgentCore only adds
# settings persistence, the enable/disable toggle, agent<->container mapping and
# cross-container aggregation.


def _effective_config() -> Any:
    from agentcore.sandbox.config import SandboxGlobalConfig

    return SandboxGlobalConfig.load_effective()


def _get_client(_request: Request | None = None) -> Any:
    """Build a standalone OpenSandboxClient from the effective config."""
    from agentcore.sandbox.client import OpenSandboxClient

    return OpenSandboxClient(_effective_config())


def _config_from_body(body: dict[str, Any] | None) -> Any:
    """Merge an optional test-request body over the effective config."""
    cfg = _effective_config()
    body = body or {}
    if body.get("domain"):
        cfg.domain = str(body["domain"])
    if body.get("protocol"):
        cfg.protocol = str(body["protocol"])
    if "api_key" in body and body["api_key"] is not None:
        cfg.api_key = body["api_key"] or None
    return cfg


def _mask_settings(cfg: Any) -> dict[str, Any]:
    return {
        "enabled": cfg.enabled,
        "domain": cfg.domain,
        "protocol": cfg.protocol,
        "default_cleanup": cfg.default_cleanup,
        "base_url": cfg.base_url,
        "api_key_set": bool(cfg.api_key),
    }


@router.get("/settings")
async def get_sandbox_settings() -> dict[str, Any]:
    """Read the effective sandbox connection settings (api_key masked)."""
    return _mask_settings(_effective_config())


@router.put("/settings")
async def update_sandbox_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Persist sandbox connection settings to ``sandbox.json``.

    Only whitelisted keys are stored.  An omitted/``None`` ``api_key`` keeps the
    currently effective key so masking round-trips don't wipe it; sending
    ``""`` clears it.  Takes effect on the next ``start`` / process restart.
    """
    from agentcore.sandbox.config import write_settings_file

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    current = _effective_config()
    stored: dict[str, Any] = {
        "enabled": bool(body["enabled"]) if "enabled" in body else current.enabled,
        "domain": str(body.get("domain") or current.domain),
        "protocol": str(body.get("protocol") or current.protocol),
        "default_cleanup": str(
            body.get("default_cleanup") or current.default_cleanup
        ),
    }
    if "api_key" in body and body["api_key"] is not None:
        stored["api_key"] = str(body["api_key"])
    else:
        stored["api_key"] = current.api_key or ""

    if stored["protocol"] not in ("http", "https"):
        raise HTTPException(status_code=400, detail="protocol must be http or https")
    if stored["default_cleanup"] not in ("on_exit", "never"):
        raise HTTPException(
            status_code=400, detail="default_cleanup must be on_exit or never"
        )

    write_settings_file(stored)
    from agentcore.sandbox.config import SandboxGlobalConfig

    return _mask_settings(SandboxGlobalConfig.load_effective())


@router.post("/settings/test")
async def test_sandbox_connection(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Probe OpenSandbox server reachability with the given/saved config."""
    import time

    from agentcore.sandbox.client import OpenSandboxClient

    cfg = _config_from_body(body)
    client = OpenSandboxClient(cfg)
    started = time.perf_counter()
    try:
        reachable = await client.health_check()
        latency_ms = int((time.perf_counter() - started) * 1000)
        version = await client.get_server_version()
        return {
            "reachable": reachable,
            "latency_ms": latency_ms if reachable else None,
            "version": version if reachable else None,
            "base_url": cfg.base_url,
            "error": None if reachable else "server unreachable",
        }
    except Exception as exc:
        return {
            "reachable": False,
            "latency_ms": None,
            "version": None,
            "base_url": cfg.base_url,
            "error": str(exc),
        }


@router.post("/start")
async def start_sandbox(request: Request) -> dict[str, Any]:
    """Enable the integration and (re)build the session manager in-process."""
    from agentcore.sandbox.config import SandboxGlobalConfig, write_settings_file
    from agentcore.sandbox.lifecycle import SandboxSessionManager

    cfg = SandboxGlobalConfig.load_effective()
    cfg.enabled = True
    stored = {
        "enabled": True,
        "domain": cfg.domain,
        "protocol": cfg.protocol,
        "api_key": cfg.api_key or "",
        "default_cleanup": cfg.default_cleanup,
    }
    write_settings_file(stored)

    # Replace any previous manager cleanly (stop its idle loop + sessions).
    old = getattr(request.app.state, "sandbox_session_manager", None)
    if old is not None:
        await old.stop_idle_checker()
        await old.destroy_all()

    manager = SandboxSessionManager(cfg)
    request.app.state.sandbox_session_manager = manager
    manager.start_idle_checker()
    health = await manager.health_check()
    logger.info("Sandbox integration enabled — %s", cfg.base_url)
    return {"status": "started", "health": health}


@router.post("/stop")
async def stop_sandbox(request: Request) -> dict[str, Any]:
    """Disable the integration: destroy sessions and drop the manager."""
    from agentcore.sandbox.config import SandboxGlobalConfig, write_settings_file

    manager = getattr(request.app.state, "sandbox_session_manager", None)
    destroyed = False
    if manager is not None:
        try:
            await manager.stop_idle_checker()
        except Exception:
            logger.exception("Failed to stop sandbox idle checker on stop")
        try:
            await manager.destroy_all()
        except Exception:
            logger.exception("Failed to destroy sandbox sessions on stop")
        destroyed = True
    request.app.state.sandbox_session_manager = None

    cfg = SandboxGlobalConfig.load_effective()
    write_settings_file(
        {
            "enabled": False,
            "domain": cfg.domain,
            "protocol": cfg.protocol,
            "api_key": cfg.api_key or "",
            "default_cleanup": cfg.default_cleanup,
        }
    )
    logger.info("Sandbox integration disabled")
    return {"status": "stopped", "sessions_destroyed": destroyed}


@router.get("/overview")
async def sandbox_overview(request: Request) -> dict[str, Any]:
    """Aggregated global status: reachability, version, container counts, CPU/mem.

    Returns a degraded (non-error) structure when disabled or unreachable so the
    control-plane page can render guidance instead of failing.
    """
    cfg = _effective_config()
    overview: dict[str, Any] = {
        "enabled": cfg.enabled,
        "server_url": cfg.base_url,
        "server_reachable": False,
        "version": None,
        "containers": {"total": 0, "running": 0, "paused": 0, "other": 0},
        "cpu": {"total_cores": None, "used_percentage": None},
        "memory": {"total_mib": None, "used_mib": None},
        "error": None,
    }
    client = _get_client(request)
    try:
        overview["server_reachable"] = await client.health_check()
    except Exception as exc:
        overview["error"] = str(exc)
        return overview
    if not overview["server_reachable"]:
        overview["error"] = "server unreachable"
        return overview

    try:
        overview["version"] = await client.get_server_version()
        infos = await client.list_sandboxes()
        counts = {"total": len(infos), "running": 0, "paused": 0, "other": 0}
        cpu_cores = 0
        cpu_used_samples: list[float] = []
        mem_total = 0.0
        mem_used = 0.0
        for info in infos:
            status = (info.status or "").lower()
            if status in ("running", "ready", "alive"):
                counts["running"] += 1
                try:
                    m = await client.get_metrics(info.sandbox_id)
                    if m.get("cpu_count"):
                        cpu_cores += int(m["cpu_count"])
                    if m.get("cpu_used_percentage") is not None:
                        cpu_used_samples.append(float(m["cpu_used_percentage"]))
                    if m.get("memory_total_in_mib") is not None:
                        mem_total += float(m["memory_total_in_mib"])
                    if m.get("memory_used_in_mib") is not None:
                        mem_used += float(m["memory_used_in_mib"])
                except Exception:
                    logger.debug("metrics failed for %s", info.sandbox_id)
            elif status == "paused":
                counts["paused"] += 1
            else:
                counts["other"] += 1
        overview["containers"] = counts
        overview["cpu"] = {
            "total_cores": cpu_cores or None,
            "used_percentage": (
                round(sum(cpu_used_samples) / len(cpu_used_samples), 1)
                if cpu_used_samples
                else None
            ),
        }
        overview["memory"] = {
            "total_mib": round(mem_total, 1) or None,
            "used_mib": round(mem_used, 1) or None,
        }
    except Exception as exc:
        overview["error"] = str(exc)
    return overview


@router.get("/containers")
async def list_containers(request: Request) -> dict[str, Any]:
    """Global container list across all agents (OpenSandbox pass-through)."""
    client = _get_client(request)
    try:
        infos = await client.list_sandboxes()
    except Exception as exc:
        return {"containers": [], "reachable": False, "error": str(exc)}
    return {
        "reachable": True,
        "containers": [
            {
                "sandbox_id": i.sandbox_id,
                "agent_id": i.agent_id,
                "image": i.image,
                "status": i.status,
                "platform": i.platform,
                "created_at": i.created_at.isoformat() if i.created_at else None,
                "expires_at": i.expires_at.isoformat() if i.expires_at else None,
            }
            for i in infos
        ],
    }


@router.get("/containers/{sandbox_id}/metrics")
async def container_metrics(sandbox_id: str, request: Request) -> dict[str, Any]:
    """Live CPU / memory metrics for one container."""
    client = _get_client(request)
    try:
        return await client.get_metrics(sandbox_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"metrics unavailable: {exc}")


@router.get("/containers/{sandbox_id}/logs")
async def container_logs(sandbox_id: str, request: Request) -> dict[str, Any]:
    """Diagnostic logs for one container."""
    client = _get_client(request)
    try:
        return {"sandbox_id": sandbox_id, "logs": await client.get_diagnostic_logs(sandbox_id)}
    except Exception as exc:
        return {"sandbox_id": sandbox_id, "logs": [], "error": str(exc)}


@router.post("/containers/{sandbox_id}/pause")
async def container_pause(sandbox_id: str, request: Request) -> dict[str, str]:
    client = _get_client(request)
    await client.pause_sandbox(sandbox_id)
    return {"status": "paused", "sandbox_id": sandbox_id}


@router.post("/containers/{sandbox_id}/resume")
async def container_resume(sandbox_id: str, request: Request) -> dict[str, str]:
    client = _get_client(request)
    await client.resume_sandbox(sandbox_id)
    return {"status": "resumed", "sandbox_id": sandbox_id}


@router.post("/containers/{sandbox_id}/renew")
async def container_renew(
    sandbox_id: str, request: Request, extra_seconds: int = 600
) -> dict[str, Any]:
    client = _get_client(request)
    await client.renew_sandbox(sandbox_id, extra_seconds)
    return {"status": "renewed", "sandbox_id": sandbox_id, "extra_seconds": extra_seconds}


@router.post("/containers/{sandbox_id}/destroy")
async def container_destroy(sandbox_id: str, request: Request) -> dict[str, str]:
    client = _get_client(request)
    await client.destroy_sandbox(sandbox_id)
    return {"status": "destroyed", "sandbox_id": sandbox_id}


@router.get("/images")
async def image_distribution(request: Request) -> dict[str, Any]:
    """Container-count per image, aggregated client-side from the list."""
    client = _get_client(request)
    try:
        infos = await client.list_sandboxes()
    except Exception as exc:
        return {"images": [], "error": str(exc)}
    counts: dict[str, int] = {}
    for i in infos:
        key = i.image or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return {
        "images": [
            {"image": img, "count": cnt}
            for img, cnt in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        ]
    }


@router.get("/server-guide")
async def server_guide() -> dict[str, Any]:
    """Return local quick-start guidance for the OpenSandbox server.

    Purely informational — never executes any command.  Commands come from the
    upstream official flow; no image tags are hard-coded here.
    """
    import shutil

    from agentcore.runtime import paths

    root = paths.get_project_root()
    return {
        "prerequisites": {
            "docker": shutil.which("docker") is not None,
            "uv": shutil.which("uv") is not None or shutil.which("uvx") is not None,
        },
        "quickstart_commands": {
            "windows": [
                "docker version",
                "uvx opensandbox-server init-config $HOME/.sandbox.toml --example docker",
                "# 将 [docker].seccomp_profile 改为 \"unconfined\"（AIO Sandbox 需要）",
                "uvx opensandbox-server",
            ],
            "linux": [
                "docker version",
                "uvx opensandbox-server init-config ~/.sandbox.toml --example docker",
                "# 将 [docker].seccomp_profile 改为 \"unconfined\"（AIO Sandbox 需要）",
                "uvx opensandbox-server",
            ],
        },
        "aio_requirements": {
            "seccomp_profile": "unconfined",
            "config_key": "[docker].seccomp_profile",
            "config_file": "~/.sandbox.toml",
            "docker_equivalent": "--security-opt seccomp=unconfined",
            "reason": (
                "AIO Sandbox 镜像内置 Chromium / Playwright / VNC，需 Docker 默认 "
                "seccomp 之外的 clone3 / unshare 等 syscall，否则浏览器工具无法启动。"
            ),
            "applies_to": "新建容器（server 重启后生效），存量 sandbox 需销毁重建。",
        },
        "script_paths": {
            "windows": str(root / "scripts" / "opensandbox-quickstart.ps1"),
            "linux": str(root / "scripts" / "opensandbox-quickstart.sh"),
        },
        "default_url": "http://localhost:8080",
        "docs_url": "https://github.com/alibaba/OpenSandbox",
    }
