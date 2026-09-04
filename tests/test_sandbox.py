"""Unit tests for the sandbox module.

Covers:
- SandboxGlobalConfig / SandboxLifecycleConfig dataclass creation
- OpenSandboxFactory error handling (server unreachable)
- _create_backend() sandbox branch routing
- SandboxSessionManager session lifecycle (mock client)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestSandboxGlobalConfig:
    """SandboxGlobalConfig from_env and defaults."""

    def test_defaults(self):
        from agentcore.sandbox.config import SandboxGlobalConfig

        cfg = SandboxGlobalConfig()
        assert cfg.domain == "localhost:8080"
        assert cfg.protocol == "http"
        assert cfg.api_key is None
        assert cfg.default_cleanup == "on_exit"
        assert cfg.enabled is False

    def test_from_env(self):
        from agentcore.sandbox.config import SandboxGlobalConfig

        env = {
            "SANDBOX_DOMAIN": "sandbox.example.com:9090",
            "SANDBOX_PROTOCOL": "https",
            "SANDBOX_API_KEY": "test-key-123",
            "SANDBOX_ENABLED": "true",
        }
        cfg = SandboxGlobalConfig.from_env(env)
        assert cfg.domain == "sandbox.example.com:9090"
        assert cfg.protocol == "https"
        assert cfg.api_key == "test-key-123"
        assert cfg.enabled is True

    def test_from_env_disabled(self):
        from agentcore.sandbox.config import SandboxGlobalConfig

        env = {"SANDBOX_ENABLED": "false"}
        cfg = SandboxGlobalConfig.from_env(env)
        assert cfg.enabled is False

    def test_from_env_empty(self):
        from agentcore.sandbox.config import SandboxGlobalConfig

        cfg = SandboxGlobalConfig.from_env({})
        assert cfg.domain == "localhost:8080"
        assert cfg.enabled is False


class TestSandboxLifecycleConfig:
    """SandboxLifecycleConfig from_dict."""

    def test_defaults(self):
        from agentcore.sandbox.config import SandboxLifecycleConfig

        cfg = SandboxLifecycleConfig()
        assert cfg.strategy == "ephemeral"
        assert cfg.timeout == 600
        assert cfg.renew_on_chat is True
        assert cfg.renew_on_execute is False
        assert cfg.max_resume_count == 10
        assert cfg.idle_timeout == 300

    def test_from_dict(self):
        from agentcore.sandbox.config import SandboxLifecycleConfig

        data = {
            "strategy": "pause-on-idle",
            "timeout": 1800,
            "renew_on_chat": False,
            "max_resume_count": 5,
        }
        cfg = SandboxLifecycleConfig.from_dict(data)
        assert cfg.strategy == "pause-on-idle"
        assert cfg.timeout == 1800
        assert cfg.renew_on_chat is False
        assert cfg.max_resume_count == 5

    def test_from_dict_empty(self):
        from agentcore.sandbox.config import SandboxLifecycleConfig

        cfg = SandboxLifecycleConfig.from_dict({})
        assert cfg.strategy == "ephemeral"
        assert cfg.timeout == 600

    def test_from_dict_none(self):
        from agentcore.sandbox.config import SandboxLifecycleConfig

        cfg = SandboxLifecycleConfig.from_dict(None)
        assert cfg.strategy == "ephemeral"


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestOpenSandboxFactory:
    """OpenSandboxFactory creation and error handling."""

    def test_factory_init_default_config(self):
        from agentcore.sandbox.config import SandboxGlobalConfig
        from agentcore.sandbox.factory import OpenSandboxFactory

        factory = OpenSandboxFactory()
        assert factory._config is not None
        assert factory._config.domain == "localhost:8080"

    def test_factory_init_custom_config(self):
        from agentcore.sandbox.config import SandboxGlobalConfig
        from agentcore.sandbox.factory import OpenSandboxFactory

        cfg = SandboxGlobalConfig(domain="custom:9999", protocol="https")
        factory = OpenSandboxFactory(config=cfg)
        assert factory._config.domain == "custom:9999"
        assert factory._config.protocol == "https"

    def test_factory_create_server_unreachable(self):
        """When the OpenSandbox server is unreachable, create() should raise."""
        from agentcore.sandbox.config import SandboxGlobalConfig
        from agentcore.sandbox.factory import OpenSandboxFactory

        cfg = SandboxGlobalConfig(domain="nonexistent:9999")
        factory = OpenSandboxFactory(config=cfg)

        # The factory should raise when the server is unreachable
        with pytest.raises(Exception):
            factory.create(agent_id="test-agent")


# ---------------------------------------------------------------------------
# Backend routing tests
# ---------------------------------------------------------------------------


class TestCreateBackendSandboxRouting:
    """_create_backend() sandbox branch routing."""

    def test_local_backend_default(self, tmp_path):
        """Without sandbox config, should return FilesystemBackend."""
        from agentcore.runtime.agent_factory import _create_backend

        settings: dict[str, Any] = {}
        backend = _create_backend(settings, workspace_dir=tmp_path)
        # Should be a FilesystemBackend (or StateBackend if no workspace)
        assert backend is not None

    def test_local_backend_with_workspace(self, tmp_path):
        """With type=local, should return FilesystemBackend."""
        from agentcore.runtime.agent_factory import _create_backend

        settings: dict[str, Any] = {"backend": {"type": "local"}}
        backend = _create_backend(settings, workspace_dir=tmp_path)
        assert backend is not None

    def test_sandbox_backend_type(self, tmp_path):
        """With type=sandbox, should attempt to use sandbox factory."""
        from agentcore.runtime.agent_factory import _create_backend

        settings: dict[str, Any] = {
            "backend": {
                "type": "sandbox",
                "provider": "opensandbox",
            }
        }
        # This should raise because the sandbox server is not available
        # (which is the expected behavior — no silent fallback)
        with pytest.raises(Exception):
            _create_backend(settings, workspace_dir=tmp_path)


# ---------------------------------------------------------------------------
# Session manager tests (mock)
# ---------------------------------------------------------------------------


class TestSandboxSessionManager:
    """SandboxSessionManager lifecycle management."""

    @pytest.fixture
    def session_manager(self):
        from agentcore.sandbox.lifecycle import SandboxSessionManager
        from agentcore.sandbox.config import SandboxGlobalConfig

        cfg = SandboxGlobalConfig(enabled=False)
        mgr = SandboxSessionManager(global_config=cfg)
        return mgr

    def test_init_empty(self, session_manager):
        """Initially no sessions."""
        sessions = session_manager.list_sessions()
        assert sessions == []

    def test_list_sessions_empty(self, session_manager):
        """list_sessions returns empty list initially."""
        assert session_manager.list_sessions() == []

    async def test_destroy_nonexistent(self, session_manager):
        """Destroying a non-existent session should not raise."""
        await session_manager.destroy("nonexistent-agent")

    async def test_destroy_all_empty(self, session_manager):
        """destroy_all with no sessions should not raise."""
        await session_manager.destroy_all()

    async def test_pause_nonexistent(self, session_manager):
        """Pausing a non-existent session should not raise."""
        await session_manager.pause("nonexistent-agent")

    async def test_resume_nonexistent(self, session_manager):
        """Resuming a non-existent session should raise or return None."""
        with pytest.raises(Exception):
            await session_manager.resume("nonexistent-agent")

    async def test_health_check_structure(self, session_manager):
        """health_check should return a dict."""
        result = await session_manager.health_check()
        assert isinstance(result, dict)


class TestSandboxIdleEnforcement:
    """check_idle() must actually enforce strategies (regression: it had
    no scheduler and every session stayed 'running' forever)."""

    @pytest.fixture
    def mgr(self):
        from agentcore.sandbox.config import SandboxGlobalConfig
        from agentcore.sandbox.lifecycle import SandboxSessionManager

        return SandboxSessionManager(
            global_config=SandboxGlobalConfig(enabled=False),
            idle_check_interval=1,
        )

    @staticmethod
    def _session(agent_id, lifecycle_cfg, status="running", idle_seconds=9999):
        from datetime import datetime, timedelta, timezone

        from agentcore.sandbox.lifecycle import SandboxSession

        now = datetime.now(timezone.utc)
        return SandboxSession(
            sandbox_id=f"sbx-{agent_id}",
            agent_id=agent_id,
            backend=AsyncMock(),
            created_at=now - timedelta(hours=1),
            last_active_at=now - timedelta(seconds=idle_seconds),
            status=status,
            lifecycle_config=lifecycle_cfg,
        )

    async def test_ephemeral_idle_destroys_session(self, mgr):
        from agentcore.sandbox.config import SandboxLifecycleConfig

        session = self._session(
            "a1", SandboxLifecycleConfig(strategy="ephemeral", idle_timeout=1),
        )
        mgr._sessions["a1"] = session
        with patch.object(mgr, "_do_destroy", new=AsyncMock()) as destroy:
            await mgr.check_idle()
        destroy.assert_awaited_once_with(session)
        assert mgr.get_session("a1") is None

    async def test_pause_on_idle_pauses_session(self, mgr):
        from agentcore.sandbox.config import SandboxLifecycleConfig

        session = self._session(
            "a2",
            SandboxLifecycleConfig(strategy="pause-on-idle", idle_timeout=1),
        )
        mgr._sessions["a2"] = session
        with patch.object(mgr, "_pause", new=AsyncMock()) as pause:
            await mgr.check_idle()
        pause.assert_awaited_once_with(session)

    async def test_persistent_idle_is_noop(self, mgr):
        from agentcore.sandbox.config import SandboxLifecycleConfig

        session = self._session(
            "a3",
            SandboxLifecycleConfig(strategy="persistent", idle_timeout=1),
        )
        mgr._sessions["a3"] = session
        with (
            patch.object(mgr, "_pause", new=AsyncMock()) as pause,
            patch.object(mgr, "_do_destroy", new=AsyncMock()) as destroy,
        ):
            await mgr.check_idle()
        pause.assert_not_awaited()
        destroy.assert_not_awaited()
        assert mgr.get_session("a3") is session

    async def test_idle_checker_loop_starts_stops_idempotently(self, mgr):
        mgr.start_idle_checker()
        task = mgr._idle_task
        assert task is not None and not task.done()
        mgr.start_idle_checker()  # idempotent — no second loop
        assert mgr._idle_task is task
        await mgr.stop_idle_checker()
        assert task.done()
        assert mgr._idle_task is None
        await mgr.stop_idle_checker()  # safe when already stopped


class TestSandboxSessionCfgSnapshot:
    """Sessions must keep the backend cfg they were created from so
    restart / resume-fallback don't downgrade e.g. AIO agents to slim."""

    async def test_create_session_stores_backend_cfg(self):
        from datetime import datetime, timezone

        from agentcore.sandbox.lifecycle import SandboxSessionManager

        mgr = SandboxSessionManager.__new__(SandboxSessionManager)
        mgr._sessions = {}
        mgr._global_config = None
        cfg = {
            "type": "sandbox",
            "provider": "aio",
            "image": "ghcr.io/agent-infra/sandbox:latest",
            "lifecycle": {"strategy": "persistent"},
        }
        fake_backend = object()
        with (
            patch(
                "agentcore.runtime.sandbox.get_sandbox_factory",
            ) as get_factory,
        ):
            get_factory.return_value.create_async = AsyncMock(
                return_value=fake_backend,
            )
            session = await mgr._create_session("agent-x", cfg)
        assert session.backend_cfg == cfg
        assert session.image == "ghcr.io/agent-infra/sandbox:latest"

    async def test_resume_fallback_reuses_snapshot(self):
        from datetime import datetime, timezone

        from agentcore.sandbox.config import SandboxLifecycleConfig
        from agentcore.sandbox.lifecycle import SandboxSession, SandboxSessionManager

        mgr = SandboxSessionManager.__new__(SandboxSessionManager)
        mgr._global_config = AsyncMock()
        now = datetime.now(timezone.utc)
        session = SandboxSession(
            sandbox_id="sbx-1",
            agent_id="agent-y",
            backend=AsyncMock(),
            created_at=now,
            last_active_at=now,
            status="paused",
            lifecycle_config=SandboxLifecycleConfig(strategy="pause-on-idle"),
            backend_cfg={"type": "sandbox", "image": "img:custom"},
        )
        recreated = object()
        with (
            patch.object(mgr, "_do_destroy", new=AsyncMock()),
            patch.object(
                mgr, "_create_session", new=AsyncMock(return_value=recreated),
            ) as create,
            patch(
                "agentcore.sandbox.client.OpenSandboxClient.resume_sandbox",
                new=AsyncMock(side_effect=RuntimeError("resume boom")),
            ),
        ):
            result = await mgr._resume(session)
        assert result is recreated
        create.assert_awaited_once()
        passed_cfg = create.await_args.args[1]
        assert passed_cfg["image"] == "img:custom"


class TestSandboxPauseResumeLifecycle:
    """Regression: paused sandboxes must be resumed on next chat,
    not destroyed and recreated.  Applies to ALL strategies, not
    just pause-on-idle."""

    @pytest.fixture
    def mgr(self):
        from agentcore.sandbox.config import SandboxGlobalConfig
        from agentcore.sandbox.lifecycle import SandboxSessionManager

        return SandboxSessionManager(
            global_config=SandboxGlobalConfig(enabled=False),
        )

    async def test_get_or_create_resumes_paused_any_strategy(self, mgr):
        """A paused session should be resumed regardless of strategy."""
        from agentcore.sandbox.config import SandboxLifecycleConfig
        from agentcore.sandbox.lifecycle import SandboxSession

        now = datetime.now(timezone.utc)
        for strategy in ("ephemeral", "pause-on-idle", "persistent"):
            session = SandboxSession(
                sandbox_id=f"sbx-{strategy}",
                agent_id=f"agent-{strategy}",
                backend=AsyncMock(),
                created_at=now,
                last_active_at=now,
                status="paused",
                lifecycle_config=SandboxLifecycleConfig(strategy=strategy),
            )
            mgr._sessions[f"agent-{strategy}"] = session

        new_backend = AsyncMock()
        for strategy in ("ephemeral", "pause-on-idle", "persistent"):
            with (
                patch(
                    "agentcore.sandbox.client.OpenSandboxClient.resume_sandbox",
                    new=AsyncMock(),
                ),
                patch.object(
                    mgr, "_recreate_backend", new=AsyncMock(return_value=new_backend),
                ),
            ):
                result = await mgr.get_or_create(
                    f"agent-{strategy}", backend_cfg={},
                )
            assert result.status == "running", f"Strategy {strategy} should resume"
            assert result.backend is new_backend

    async def test_get_or_create_detects_server_side_pause(self, mgr):
        """If session says 'running' but server says 'paused', resume."""
        from agentcore.sandbox.config import SandboxLifecycleConfig
        from agentcore.sandbox.lifecycle import SandboxSession

        now = datetime.now(timezone.utc)
        session = SandboxSession(
            sandbox_id="sbx-z",
            agent_id="agent-z",
            backend=AsyncMock(),
            created_at=now,
            last_active_at=now,
            status="running",  # stale — actually paused server-side
            lifecycle_config=SandboxLifecycleConfig(strategy="pause-on-idle"),
        )
        mgr._sessions["agent-z"] = session

        new_backend = AsyncMock()
        with (
            patch.object(
                mgr, "_get_remote_status", new=AsyncMock(return_value="paused"),
            ),
            patch(
                "agentcore.sandbox.client.OpenSandboxClient.resume_sandbox",
                new=AsyncMock(),
            ),
            patch.object(
                mgr, "_recreate_backend", new=AsyncMock(return_value=new_backend),
            ),
        ):
            result = await mgr.get_or_create("agent-z", backend_cfg={})
        assert result.status == "running"
        assert result.backend is new_backend

    async def test_get_or_create_recreates_when_dead(self, mgr):
        """If remote status is None (not found), recreate."""
        from agentcore.sandbox.config import SandboxLifecycleConfig
        from agentcore.sandbox.lifecycle import SandboxSession

        now = datetime.now(timezone.utc)
        session = SandboxSession(
            sandbox_id="sbx-dead",
            agent_id="agent-dead",
            backend=AsyncMock(),
            created_at=now,
            last_active_at=now,
            status="running",
            lifecycle_config=SandboxLifecycleConfig(strategy="persistent"),
        )
        mgr._sessions["agent-dead"] = session

        new_session = SandboxSession(
            sandbox_id="sbx-new",
            agent_id="agent-dead",
            backend=AsyncMock(),
            created_at=now,
            last_active_at=now,
            status="running",
        )
        with (
            patch.object(
                mgr, "_get_remote_status", new=AsyncMock(return_value=None),
            ),
            patch.object(mgr, "destroy", new=AsyncMock()),
            patch.object(
                mgr, "_create_session", new=AsyncMock(return_value=new_session),
            ),
        ):
            result = await mgr.get_or_create("agent-dead", backend_cfg={})
        assert result is new_session
        assert result._sandbox_recreated is True

    async def test_resume_refreshes_backend(self):
        """_resume should call _recreate_backend to get fresh endpoints."""
        from agentcore.sandbox.config import SandboxLifecycleConfig
        from agentcore.sandbox.lifecycle import SandboxSession, SandboxSessionManager

        mgr = SandboxSessionManager.__new__(SandboxSessionManager)
        mgr._global_config = AsyncMock()
        now = datetime.now(timezone.utc)
        old_backend = AsyncMock()
        session = SandboxSession(
            sandbox_id="sbx-r",
            agent_id="agent-r",
            backend=old_backend,
            created_at=now,
            last_active_at=now,
            status="paused",
            lifecycle_config=SandboxLifecycleConfig(strategy="pause-on-idle"),
        )
        new_backend = AsyncMock()
        with (
            patch(
                "agentcore.sandbox.client.OpenSandboxClient.resume_sandbox",
                new=AsyncMock(),
            ),
            patch.object(
                mgr, "_recreate_backend", new=AsyncMock(return_value=new_backend),
            ),
        ):
            result = await mgr._resume(session)
        assert result.status == "running"
        assert result.backend is new_backend  # backend was refreshed
        assert result.resume_count == 1

    async def test_resume_fallback_keeps_old_backend_on_recreate_failure(self):
        """If _recreate_backend returns None, keep the old backend."""
        from agentcore.sandbox.config import SandboxLifecycleConfig
        from agentcore.sandbox.lifecycle import SandboxSession, SandboxSessionManager

        mgr = SandboxSessionManager.__new__(SandboxSessionManager)
        mgr._global_config = AsyncMock()
        now = datetime.now(timezone.utc)
        old_backend = AsyncMock()
        session = SandboxSession(
            sandbox_id="sbx-f",
            agent_id="agent-f",
            backend=old_backend,
            created_at=now,
            last_active_at=now,
            status="paused",
            lifecycle_config=SandboxLifecycleConfig(strategy="pause-on-idle"),
        )
        with (
            patch(
                "agentcore.sandbox.client.OpenSandboxClient.resume_sandbox",
                new=AsyncMock(),
            ),
            patch.object(
                mgr, "_recreate_backend", new=AsyncMock(return_value=None),
            ),
        ):
            result = await mgr._resume(session)
        assert result.status == "running"
        assert result.backend is old_backend  # kept old backend


# ---------------------------------------------------------------------------
# Sync engine container_id tests
# ---------------------------------------------------------------------------


class TestSyncEngineContainerId:
    """Manifest must reset when the container id changes so that a
    recreated (empty) container gets a full re-upload instead of the
    push() method skipping every file as 'unchanged'."""

    def test_note_container_id_resets_manifest_on_change(self, tmp_path):
        from agentcore.sandbox.sync_engine import SandboxSyncEngine

        # Create a workspace file
        (tmp_path / "agent.md").write_text("# Agent")

        engine = SandboxSyncEngine(tmp_path)
        # First container
        engine.note_container_id("container-1")
        assert engine._manifest.get("container_id") == "container-1"
        assert engine._manifest.get("files") == {}

        # Simulate a previous sync: add a file entry to the manifest
        engine._manifest["files"] = {"agent.md": {"sha256": "abc", "size": 7}}
        engine._save_manifest()

        # Same container — no reset
        engine.note_container_id("container-1")
        assert "agent.md" in engine._manifest["files"]

        # Different container — manifest files cleared
        engine.note_container_id("container-2")
        assert engine._manifest.get("container_id") == "container-2"
        assert engine._manifest.get("files") == {}
        assert engine._manifest.get("last_sync_at") is None

    def test_note_container_id_idempotent_for_same_container(self, tmp_path):
        from agentcore.sandbox.sync_engine import SandboxSyncEngine

        engine = SandboxSyncEngine(tmp_path)
        engine.note_container_id("c-1")
        engine._manifest["files"] = {"x": {"sha256": "1"}}
        engine._save_manifest()

        # Calling again with the same id should not reset
        engine.note_container_id("c-1")
        assert "x" in engine._manifest["files"]

    def test_push_uploads_all_files_after_container_change(self, tmp_path):
        """After container change, push() must re-upload every file."""
        import asyncio
        from agentcore.sandbox.sync_engine import SandboxSyncEngine

        (tmp_path / "agent.md").write_text("# Agent")
        (tmp_path / "bootstrap.md").write_text("# Bootstrap")

        engine = SandboxSyncEngine(tmp_path)

        # Simulate old container sync
        engine._manifest["container_id"] = "old-container"
        engine._manifest["files"] = {
            "agent.md": {"sha256": "old-hash", "size": 7},
            "bootstrap.md": {"sha256": "old-hash2", "size": 11},
        }
        engine._save_manifest()

        # New container — note_container_id resets manifest
        engine.note_container_id("new-container")

        # Mock backend
        uploaded = []
        mock_backend = AsyncMock()
        mock_backend.aupload_files = AsyncMock(
            side_effect=lambda files: [
                type("R", (), {"path": p, "error": None})()
                for p, _ in files
            ]
        )

        report = asyncio.run(engine.push(mock_backend))
        assert len(report["pushed"]) == 2  # Both files uploaded

    def test_reset_manifest_forces_full_reupload(self, tmp_path):
        """reset_manifest() clears file entries so next push re-uploads."""
        from agentcore.sandbox.sync_engine import SandboxSyncEngine

        (tmp_path / "agent.md").write_text("# Agent")
        engine = SandboxSyncEngine(tmp_path)
        engine.note_container_id("ctr-1")

        # Simulate a completed sync
        engine._manifest["files"] = {"agent.md": {"sha256": "abc", "size": 7}}
        engine._manifest["last_sync_at"] = "2026-01-01T00:00:00Z"
        engine._save_manifest()

        # reset_manifest should clear files but keep container_id
        engine.reset_manifest()
        assert engine._manifest["files"] == {}
        assert engine._manifest["last_sync_at"] is None
        assert engine._manifest["container_id"] == "ctr-1"

        # Next push should re-upload (mock backend)
        mock_backend = AsyncMock()
        mock_backend.aupload_files = AsyncMock(
            return_value=[type("R", (), {"path": "agent.md", "error": None})()]
        )
        report = asyncio.run(engine.push(mock_backend))
        assert len(report["pushed"]) == 1

    def test_push_reuploads_when_container_empty_but_manifest_stale(self, tmp_path):
        """push() must re-upload files when the container is empty even
        though the manifest claims they are already synced."""
        from agentcore.sandbox.sync_engine import SandboxSyncEngine

        (tmp_path / "agent.md").write_text("# Agent")
        (tmp_path / "bootstrap.md").write_text("# Bootstrap")
        engine = SandboxSyncEngine(tmp_path)

        # Simulate a previous successful sync — manifest has hashes
        real_sha_agent = engine._sha256_file(tmp_path / "agent.md")
        real_sha_boot = engine._sha256_file(tmp_path / "bootstrap.md")
        engine._manifest["files"] = {
            "agent.md": {"sha256": real_sha_agent, "size": 7},
            "bootstrap.md": {"sha256": real_sha_boot, "size": 11},
        }
        engine._save_manifest()

        # Mock backend: container is EMPTY (no files)
        mock_backend = AsyncMock()
        # _collect_remote_files finds nothing → empty container
        engine._collect_remote_files = AsyncMock(return_value=None)
        # Patch to call the real method but with empty result
        async def _empty_collect(backend, abs_dir, rel_prefix, out):
            pass  # out stays empty → container has no files
        engine._collect_remote_files = _empty_collect

        # aupload_files should be called with both files
        uploaded = []
        async def _mock_upload(files):
            for path, _ in files:
                uploaded.append(path)
            return [type("R", (), {"path": p, "error": None})() for p, _ in files]
        mock_backend.aupload_files = _mock_upload

        report = asyncio.run(engine.push(mock_backend))
        # Both files must be re-uploaded despite manifest saying "synced"
        assert len(report["pushed"]) == 2
        assert len(uploaded) == 2


# ---------------------------------------------------------------------------
# Seeder tests
# ---------------------------------------------------------------------------


class TestSeeder:
    """Workspace → Sandbox file sync."""

    async def test_seed_sandbox_no_workspace(self, tmp_path):
        """seed_sandbox with empty workspace should not fail."""
        from agentcore.sandbox.seeder import seed_sandbox

        mock_backend = AsyncMock()
        # Empty workspace dir
        await seed_sandbox(mock_backend, tmp_path)
        # No seed files to upload, should complete without error

    async def test_seed_sandbox_with_kernel_files(self, tmp_path):
        """seed_sandbox should upload kernel files if they exist."""
        from agentcore.sandbox.seeder import seed_sandbox

        # Create some kernel files
        (tmp_path / "agent.md").write_text("# Agent")
        (tmp_path / "bootstrap.md").write_text("# Bootstrap")

        mock_backend = AsyncMock()
        mock_backend.write_file = AsyncMock()

        await seed_sandbox(mock_backend, tmp_path)
        # Should have written files without error


# ---------------------------------------------------------------------------
# Client tests
# ---------------------------------------------------------------------------


class TestOpenSandboxClient:
    """OpenSandboxClient basic operations."""

    def test_client_init(self):
        from agentcore.sandbox.config import SandboxGlobalConfig
        from agentcore.sandbox.client import OpenSandboxClient

        cfg = SandboxGlobalConfig(domain="test:8080")
        client = OpenSandboxClient(cfg)
        assert client._config.domain == "test:8080"

    async def test_health_check_unreachable(self):
        from agentcore.sandbox.config import SandboxGlobalConfig
        from agentcore.sandbox.client import OpenSandboxClient

        cfg = SandboxGlobalConfig(domain="nonexistent:9999")
        client = OpenSandboxClient(cfg)
        result = await client.health_check()
        assert result is False
