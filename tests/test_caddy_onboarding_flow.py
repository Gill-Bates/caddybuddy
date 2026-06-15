#!/usr/bin/env python3
#
# tests/test_caddy_onboarding_flow.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import errno
import os
_ENV_OVERRIDES = {
    "CB_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CB_ADMIN_PASSWORD": "UnitTestPassword-123A",
    "CADDYBUDDY_ADMIN_PASSWORD": "UnitTestPassword-123A",
}
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}

for key, value in _ENV_OVERRIDES.items():
    os.environ[key] = value

from app.config.settings import get_settings
get_settings.cache_clear()

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.services.caddyfile_manager as caddyfile_manager

def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()
from app.models.base import Base
from app.models.entities import CaddyBuddyState, CaddyfileSnapshot, Site
from app.services.caddy import CaddyServiceError


class CaddyResultPolicyTests(unittest.TestCase):
    def test_sync_succeeded_classifies_known_sync_statuses(self) -> None:
        self.assertTrue(caddyfile_manager.sync_succeeded("synced"))
        self.assertTrue(caddyfile_manager.sync_succeeded("no_change"))
        self.assertFalse(caddyfile_manager.sync_succeeded("sync_failed"))
        self.assertFalse(caddyfile_manager.sync_succeeded("validation_failed"))

    def test_onboarding_result_should_commit_classifies_known_onboarding_statuses(self) -> None:
        for status in ("onboarded", "already_managed", "synced", "no_change"):
            with self.subTest(status=status):
                self.assertTrue(caddyfile_manager.onboarding_result_should_commit(status))

        for status in ("error", "onboarding_failed", "sync_failed", "validation_failed"):
            with self.subTest(status=status):
                self.assertFalse(caddyfile_manager.onboarding_result_should_commit(status))


class CaddyOnboardingFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self._temp_dir.name)
        self.current_caddyfile_path = self.temp_path / "Caddyfile"
        database_path = self.temp_path / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
        self._runtime_settings_patcher = patch.object(
            caddyfile_manager,
            "get_caddy_config",
            new=AsyncMock(side_effect=self._get_caddy_config),
        )
        self._runtime_settings_patcher.start()

    async def asyncTearDown(self) -> None:
        self._runtime_settings_patcher.stop()
        await self.engine.dispose()
        self._temp_dir.cleanup()

    def _settings(self, caddyfile_path: Path) -> SimpleNamespace:
        return SimpleNamespace(
            caddyfile_path=caddyfile_path,
            caddy_admin_url="http://admin.example.com:2019",
            caddy_admin_timeout_seconds=5.0,
            data_dir=self.temp_path / "data",
            caddy_baseline_caddyfile="",
        )

    def _caddy_config(self, caddyfile_path: Path) -> SimpleNamespace:
        return SimpleNamespace(
            admin_url="http://admin.example.com:2019",
            caddyfile_path=caddyfile_path,
            caddyfile_path_str=str(caddyfile_path),
        )

    async def _get_caddy_config(self, _session: AsyncSession) -> SimpleNamespace:
        return self._caddy_config(self.current_caddyfile_path)

    async def _snapshot_count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count(CaddyfileSnapshot.id)))
        return int(result.scalar_one())

    async def test_runtime_status_reports_missing_caddyfile(self) -> None:
        caddyfile_path = self.temp_path / "missing.Caddyfile"
        self.current_caddyfile_path = caddyfile_path

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
            ):
                status = await caddyfile_manager.get_caddy_runtime_status(session)

        self.assertFalse(status.managed)
        self.assertFalse(status.onboarding_required)
        self.assertEqual(status.error, "caddyfile_missing")

    async def test_runtime_status_reports_not_writable_caddyfile(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        self.current_caddyfile_path = caddyfile_path
        caddyfile_path.write_text('example.com {\n    respond "ok" 200\n}\n', encoding="utf-8")

        # Mock the file open to simulate permission denied on r+ open
        original_open = Path.open

        def mock_open(self, mode="r", *args, **kwargs):
            if "r+" in mode and str(self) == str(caddyfile_path):
                raise PermissionError("Mocked permission denied")
            return original_open(self, mode, *args, **kwargs)

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(Path, "open", mock_open),
            ):
                status = await caddyfile_manager.get_caddy_runtime_status(session)

        self.assertFalse(status.managed)
        self.assertFalse(status.onboarding_required)
        self.assertEqual(status.error, "caddyfile_not_writable")

    async def test_runtime_status_reports_too_large_caddyfile(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        self.current_caddyfile_path = caddyfile_path
        caddyfile_path.write_text("x" * (caddyfile_manager._MAX_CADDYFILE_BYTES + 1), encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
            ):
                status = await caddyfile_manager.get_caddy_runtime_status(session)

        self.assertFalse(status.managed)
        self.assertFalse(status.onboarding_required)
        self.assertEqual(status.error, "caddyfile_too_large")

    async def test_get_baseline_caddyfile_falls_back_to_unmanaged_config_snippets(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        self.current_caddyfile_path = caddyfile_path
        caddyfile_path.write_text(
            """{
    admin 127.0.0.1:2019
}

(security_headers) {
    header X-Frame-Options DENY
}

example.com {
    import security_headers
    respond \"ok\" 200
}
""",
            encoding="utf-8",
        )

        async with self.session_factory() as session:
            baseline = await caddyfile_manager.get_baseline_caddyfile(session)

        self.assertIn("admin 127.0.0.1:2019", baseline)
        self.assertIn("(security_headers)", baseline)
        self.assertNotIn("example.com {", baseline)

    async def test_get_baseline_caddyfile_reads_snapshot_by_sha256(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        self.current_caddyfile_path = caddyfile_path
        caddyfile_path.write_text(caddyfile_manager.MANAGED_CADDYFILE_MARKER + "\n", encoding="utf-8")

        snapshot_content = """{
    admin 127.0.0.1:2019
}

(security_headers) {
    header X-Frame-Options DENY
}
"""

        async with self.session_factory() as session:
            snapshot = CaddyfileSnapshot(
                content=snapshot_content,
                sha256="a" * 64,
                source_path=str(caddyfile_path),
            )
            session.add(snapshot)
            session.add(CaddyBuddyState(key="snapshot_sha256", value=snapshot.sha256))
            await session.commit()

            baseline = await caddyfile_manager.get_baseline_caddyfile(session)

        self.assertIn("admin 127.0.0.1:2019", baseline)
        self.assertIn("(security_headers)", baseline)

    async def test_onboard_imports_snapshot_replaces_marker_and_syncs(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        self.current_caddyfile_path = caddyfile_path
        original = 'example.com {\n    respond "ok" 200\n}\n'
        caddyfile_path.write_text(original, encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "_adapt_caddyfile_raw",
                    new=AsyncMock(return_value=({"apps": {}}, [])),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ) as load_config,
            ):
                result = await caddyfile_manager.onboard_caddy(session)
                await session.commit()
                baseline = await caddyfile_manager.get_baseline_caddyfile(session)
                snapshot_count = await self._snapshot_count(session)

                # Verify site was imported
                site_result = await session.execute(select(Site).where(Site.domain == "example.com"))
                site = site_result.scalar_one_or_none()

        self.assertEqual(result.status, "onboarded")
        self.assertTrue(result.synced)
        self.assertEqual(snapshot_count, 1)
        # Baseline should only contain global config (empty in this case since only a site was defined)
        self.assertEqual(baseline, "")
        # Site should have been imported to the database
        self.assertIsNotNone(site)
        self.assertEqual(site.domain, "example.com")
        self.assertIn('respond "ok" 200', site.caddy_directives)
        self.assertTrue(
            caddyfile_path.read_text(encoding="utf-8").startswith(caddyfile_manager.MANAGED_CADDYFILE_MARKER)
        )
        load_config.assert_awaited_once()

    async def test_onboard_preserves_global_config_and_snippets(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        self.current_caddyfile_path = caddyfile_path
        original = """{
    admin 127.0.0.1:2019
}

(security_headers) {
    header X-Frame-Options DENY
}

example.com {
    import security_headers
    respond "ok" 200
}
"""
        caddyfile_path.write_text(original, encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "_adapt_caddyfile_raw",
                    new=AsyncMock(return_value=({"apps": {}}, [])),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ),
            ):
                result = await caddyfile_manager.onboard_caddy(session)
                await session.commit()
                baseline = await caddyfile_manager.get_baseline_caddyfile(session)

                # Verify site was imported
                site_result = await session.execute(select(Site).where(Site.domain == "example.com"))
                site = site_result.scalar_one_or_none()

        self.assertEqual(result.status, "onboarded")
        # Baseline should contain global block and snippets but not the site
        self.assertIn("admin 127.0.0.1:2019", baseline)
        self.assertIn("(security_headers)", baseline)
        self.assertNotIn("example.com {", baseline)
        # Site should have been imported
        self.assertIsNotNone(site)
        self.assertEqual(site.domain, "example.com")

    async def test_managed_marker_short_circuits_repeated_onboarding(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        self.current_caddyfile_path = caddyfile_path
        caddyfile_path.write_text('example.com {\n    respond "ok" 200\n}\n', encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "_adapt_caddyfile_raw",
                    new=AsyncMock(return_value=({"apps": {}}, [])),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ),
            ):
                first = await caddyfile_manager.onboard_caddy(session)
                await session.commit()

            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ) as load_config,
            ):
                second = await caddyfile_manager.onboard_caddy(session)
                await session.commit()
                snapshot_count = await self._snapshot_count(session)

        self.assertEqual(first.status, "onboarded")
        self.assertEqual(second.status, "already_managed")
        self.assertEqual(snapshot_count, 1)
        load_config.assert_not_awaited()

    def test_write_caddyfile_sync_restores_original_on_inplace_failure(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        original = "example.com {}\n"
        caddyfile_path.write_text(original, encoding="utf-8")

        fsync_calls = 0
        real_fsync = os.fsync

        def fake_fsync(fd):
            nonlocal fsync_calls
            fsync_calls += 1
            if fsync_calls == 2:
                raise OSError(errno.EIO, "simulated disk failure")
            return real_fsync(fd)

        with (
            patch.object(caddyfile_manager.Path, "replace", side_effect=OSError(errno.EBUSY, "simulated bind mount")),
            patch.object(caddyfile_manager.os, "fsync", side_effect=fake_fsync),
        ):
            with self.assertRaises(OSError):
                caddyfile_manager._write_caddyfile_sync(caddyfile_path, "managed\ncontent\n")

        self.assertEqual(caddyfile_path.read_text(encoding="utf-8"), original)

    async def test_operation_guard_times_out_when_file_lock_cannot_be_acquired(self) -> None:
        lock_path = self.temp_path / "data" / ".caddybuddy.caddy.lock"

        with (
            patch.object(caddyfile_manager, "get_settings", return_value=self._settings(self.current_caddyfile_path)),
            patch.object(caddyfile_manager, "fcntl", SimpleNamespace(LOCK_EX=1, LOCK_NB=2, LOCK_UN=8, flock=lambda *args: None)),
            patch.object(caddyfile_manager, "_LOCK_TIMEOUT_SECONDS", 0.0),
            patch.object(caddyfile_manager, "_try_acquire_file_lock", return_value=False),
        ):
            with self.assertRaisesRegex(TimeoutError, "Timed out waiting for Caddy operation lock"):
                async with caddyfile_manager._acquire_operation_guard():
                    self.fail("lock acquisition should have timed out")

        self.assertTrue(lock_path.parent.exists())

    async def test_sync_is_noop_when_hash_is_unchanged(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text('example.com {\n    respond "ok" 200\n}\n', encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "_adapt_caddyfile_raw",
                    new=AsyncMock(return_value=({"apps": {}}, [])),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ),
            ):
                await caddyfile_manager.onboard_caddy(session)
                await session.commit()

            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ) as load_config,
            ):
                result = await caddyfile_manager.sync_caddy_configuration(session)
                await session.commit()

        self.assertEqual(result.status, "no_change")
        self.assertFalse(result.synced)
        load_config.assert_not_awaited()

    async def test_sync_repairs_stale_caddyfile_even_when_hash_is_unchanged(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text('example.com {\n    respond "ok" 200\n}\n', encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "_adapt_caddyfile_raw",
                    new=AsyncMock(return_value=({"apps": {}}, [])),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ),
            ):
                await caddyfile_manager.onboard_caddy(session)
                await session.commit()

            caddyfile_path.write_text(
                caddyfile_manager.MANAGED_CADDYFILE_MARKER + "\n# stale\n",
                encoding="utf-8",
            )

            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "_adapt_caddyfile_raw",
                    new=AsyncMock(return_value=({"apps": {}}, [])),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ) as load_config,
            ):
                result = await caddyfile_manager.sync_caddy_configuration(session)
                await session.commit()

        self.assertEqual(result.status, "synced")
        load_config.assert_awaited_once()
        self.assertIn('respond "ok" 200', caddyfile_path.read_text(encoding="utf-8"))

    async def test_sync_loads_new_config_when_sqlite_changes(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text('example.com {\n    respond "ok" 200\n}\n', encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "_adapt_caddyfile_raw",
                    new=AsyncMock(return_value=({"apps": {}}, [])),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ),
            ):
                await caddyfile_manager.onboard_caddy(session)
                await session.commit()

            await caddyfile_manager.set_baseline_caddyfile(
                session,
                'example.com {\n    respond "changed" 200\n}\n',
            )
            await session.commit()

            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "_adapt_caddyfile_raw",
                    new=AsyncMock(return_value=({"apps": {"http": {}}}, [])),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ) as load_config,
            ):
                result = await caddyfile_manager.sync_caddy_configuration(session)
                await session.commit()

        self.assertEqual(result.status, "synced")
        self.assertTrue(result.synced)
        load_config.assert_awaited_once()

    async def test_onboard_fails_cleanly_when_admin_api_is_unavailable(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text('example.com {\n    respond "ok" 200\n}\n', encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=False)),
            ):
                result = await caddyfile_manager.onboard_caddy(session)
                await session.commit()

        self.assertEqual(result.status, "error")
        self.assertEqual(result.error_code, "caddy_admin_unavailable")

    async def test_import_mounted_caddyfile_if_needed_rolls_back_on_failure(self) -> None:
        session = SimpleNamespace(rollback=AsyncMock())

        with patch.object(
            caddyfile_manager,
            "onboard_caddy",
            new=AsyncMock(return_value=SimpleNamespace(status="error")),
        ):
            result = await caddyfile_manager.import_mounted_caddyfile_if_needed(session)

        self.assertFalse(result)
        session.rollback.assert_awaited_once()

    async def test_sync_reports_admin_api_timeout(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text('example.com {\n    respond "ok" 200\n}\n', encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "_adapt_caddyfile_raw",
                    new=AsyncMock(return_value=({"apps": {}}, [])),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ),
            ):
                await caddyfile_manager.onboard_caddy(session)
                await session.commit()

            await caddyfile_manager.set_baseline_caddyfile(
                session,
                'example.com {\n    respond "changed" 200\n}\n',
            )
            await session.commit()

            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "_adapt_caddyfile_raw",
                    new=AsyncMock(return_value=({"apps": {}}, [])),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(side_effect=CaddyServiceError("Caddy Admin API request timed out.")),
                ),
            ):
                result = await caddyfile_manager.sync_caddy_configuration(session)
                await session.commit()

        self.assertEqual(result.status, "sync_failed")
        self.assertEqual(result.error_code, "caddy_admin_unavailable")
        # The raw Admin API detail is logged, not surfaced; the result stays generic.
        self.assertEqual(result.error, "Caddy Admin API unavailable.")

    async def test_sync_reports_admin_api_http_error(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text('example.com {\n    respond "ok" 200\n}\n', encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "_adapt_caddyfile_raw",
                    new=AsyncMock(return_value=({"apps": {}}, [])),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ),
            ):
                await caddyfile_manager.onboard_caddy(session)
                await session.commit()

            await caddyfile_manager.set_baseline_caddyfile(
                session,
                'example.com {\n    respond "changed again" 200\n}\n',
            )
            await session.commit()

            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "_adapt_caddyfile_raw",
                    new=AsyncMock(return_value=({"apps": {}}, [])),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(side_effect=CaddyServiceError("Caddy Admin API request failed with status 500.")),
                ),
            ):
                result = await caddyfile_manager.sync_caddy_configuration(session)
                await session.commit()

        self.assertEqual(result.status, "sync_failed")
        self.assertEqual(result.error_code, "caddy_admin_unavailable")
        # The raw Admin API detail is logged, not surfaced; the result stays generic.
        self.assertEqual(result.error, "Caddy Admin API unavailable.")

    async def test_validate_rendered_config_uses_runtime_admin_url(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text(caddyfile_manager.MANAGED_CADDYFILE_MARKER + "\n", encoding="utf-8")
        self.current_caddyfile_path = caddyfile_path
        caddy_settings = SimpleNamespace(caddy_api_url="http://caddy:2019", caddy_admin_timeout_seconds=4.0)

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch("app.services.caddy.get_settings", return_value=caddy_settings),
                patch.object(
                    caddyfile_manager,
                    "get_caddy_config",
                    new=AsyncMock(return_value=SimpleNamespace(
                        admin_url="http://caddy:2019",
                        caddyfile_path=caddyfile_path,
                        caddyfile_path_str=str(caddyfile_path),
                    )),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "adapt_caddyfile",
                    new=AsyncMock(return_value=({"apps": {}}, [])),
                ) as adapt_mock,
            ):
                result = await caddyfile_manager.validate_rendered_caddy_configuration(session)

        self.assertEqual(result.status, "validated")
        adapt_mock.assert_awaited_once()

    async def test_sync_adapts_via_admin_client_and_calls_load_config_force(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text(caddyfile_manager.MANAGED_CADDYFILE_MARKER + "\n", encoding="utf-8")
        self.current_caddyfile_path = caddyfile_path
        caddy_settings = SimpleNamespace(caddy_api_url="http://caddy:2019", caddy_admin_timeout_seconds=4.0)

        async with self.session_factory() as session:
            site = Site(
                site_name="App",
                domain="app.example.com",
                upstream_url="http://backend:8080",
                caddy_directives='respond "ok" 200',
                enabled=True,
            )
            session.add(site)
            await session.commit()

            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch("app.services.caddy.get_settings", return_value=caddy_settings),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "adapt_caddyfile",
                    new=AsyncMock(return_value=({"apps": {"http": {}}}, [])),
                ) as adapt_mock,
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config_force",
                    new=AsyncMock(return_value=None),
                ) as load_mock,
            ):
                result = await caddyfile_manager.sync_caddy_configuration(session, force=True)

        self.assertEqual(result.status, "synced")
        adapt_mock.assert_awaited_once()
        load_mock.assert_awaited_once_with({"apps": {"http": {}}}, force_reload=True)
        rendered_caddyfile = adapt_mock.await_args.args[0]
        self.assertIn("app.example.com", rendered_caddyfile)

    async def test_parallel_onboarding_is_serialized(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text('example.com {\n    respond "ok" 200\n}\n', encoding="utf-8")

        async def run_onboarding() -> str:
            async with self.session_factory() as session:
                result = await caddyfile_manager.onboard_caddy(session)
                await session.commit()
                return result.status

        with (
            patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
            patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
            patch.object(
                caddyfile_manager.caddy_service,
                "_adapt_caddyfile_raw",
                new=AsyncMock(return_value=({"apps": {}}, [])),
            ),
            patch.object(
                caddyfile_manager.CaddyAdminClient,
                "load_config_force",
                new=AsyncMock(return_value=None),
            ) as load_config,
        ):
            first_status, second_status = await asyncio.gather(run_onboarding(), run_onboarding())

        async with self.session_factory() as session:
            snapshot_count = await self._snapshot_count(session)

        load_config.assert_awaited_once()
        self.assertEqual(snapshot_count, 1)
        self.assertEqual({first_status, second_status}, {"onboarded", "already_managed"})


@pytest.mark.anyio
async def test_sync_restores_previous_caddyfile_when_admin_load_fails() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        caddyfile_path = temp_path / "Caddyfile"
        previous_content = (
            caddyfile_manager.MANAGED_CADDYFILE_MARKER
            + "\nexample.com {\n    respond \"ok\" 200\n}\n"
        )
        caddyfile_path.write_text(previous_content, encoding="utf-8")

        session = SimpleNamespace(flush=AsyncMock())
        version = SimpleNamespace(synced_at=None)

        async def build_full_caddyfile(_session):
            return 'example.com {\n    respond "changed" 200\n}\n'

        async def adapt_caddyfile_to_json_with_format_check(*_args, **_kwargs):
            return {"apps": {}}, False

        async def inspect_caddyfile(*_args, **_kwargs):
            return None, previous_content, True

        written_content = {"value": previous_content}

        def write_caddyfile(path, content):
            written_content["value"] = content
            path.write_text(content, encoding="utf-8")

        async def direct_to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        class FakeClient:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

            async def load_config_force(self, *_args, **_kwargs) -> None:
                raise CaddyServiceError("load failed")

        with (
            patch.object(caddyfile_manager, "get_settings", return_value=SimpleNamespace(caddy_admin_timeout_seconds=5.0)),
            patch.object(
                caddyfile_manager,
                "get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(
                    admin_url="http://admin.example.com:2019",
                    caddyfile_path=caddyfile_path,
                    caddyfile_path_str=str(caddyfile_path),
                )),
            ),
            patch.object(
                caddyfile_manager,
                "build_full_caddyfile",
                new=build_full_caddyfile,
            ),
            patch.object(caddyfile_manager, "_inspect_caddyfile", new=inspect_caddyfile),
            patch.object(caddyfile_manager, "_get_state_value", new=AsyncMock(return_value=None)),
            patch.object(caddyfile_manager, "_set_state_value", new=AsyncMock()),
            patch.object(caddyfile_manager, "_clear_state_value", new=AsyncMock()),
            patch.object(caddyfile_manager, "_ensure_config_version", new=AsyncMock(return_value=version)),
            patch.object(caddyfile_manager, "_record_sync_event"),
            patch.object(caddyfile_manager, "_write_caddyfile_sync", new=write_caddyfile),
            patch.object(caddyfile_manager.asyncio, "to_thread", new=direct_to_thread),
            patch.object(
                caddyfile_manager.caddy_service,
                "adapt_caddyfile_to_json_with_format_check",
                new=adapt_caddyfile_to_json_with_format_check,
            ),
            patch.object(caddyfile_manager, "CaddyAdminClient", FakeClient),
        ):
            result = await caddyfile_manager._sync_caddy_configuration_locked(session, force=True)

    assert result.status == "sync_failed"
    assert result.error_code == "caddy_admin_unavailable"
    assert written_content["value"] == previous_content


if __name__ == "__main__":
    unittest.main()
