#!/usr/bin/env python3
#
# tests/test_caddy_onboarding_flow.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.services.caddyfile_manager as caddyfile_manager
from app.models.base import Base
from app.models.entities import CaddyfileSnapshot, Site
from app.services.caddy import CaddyServiceError


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
                    "adapt_caddyfile_to_json",
                    new=AsyncMock(return_value={"apps": {}}),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config",
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
                    "adapt_caddyfile_to_json",
                    new=AsyncMock(return_value={"apps": {}}),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config",
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

    async def test_onboard_keeps_original_caddyfile_when_sync_load_fails(self) -> None:
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
                    "adapt_caddyfile_to_json",
                    new=AsyncMock(return_value={"apps": {}}),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config",
                    new=AsyncMock(side_effect=CaddyServiceError("load failed")),
                ),
            ):
                result = await caddyfile_manager.onboard_caddy(session)
                await session.commit()

        self.assertEqual(result.status, caddyfile_manager._STATUS_ONBOARDING_FAILED)
        self.assertEqual(caddyfile_path.read_text(encoding="utf-8"), original)

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
                    "adapt_caddyfile_to_json",
                    new=AsyncMock(return_value={"apps": {}}),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config",
                    new=AsyncMock(return_value=None),
                ),
            ):
                first = await caddyfile_manager.onboard_caddy(session)
                await session.commit()

            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config",
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

    def test_replace_caddyfile_with_marker_writes_marker_and_cleans_tempfile(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text("example.com {}\n", encoding="utf-8")

        caddyfile_manager.replace_caddyfile_with_marker(caddyfile_path)

        self.assertTrue(
            caddyfile_path.read_text(encoding="utf-8").startswith(caddyfile_manager.MANAGED_CADDYFILE_MARKER)
        )
        self.assertFalse((self.temp_path / ".Caddyfile.caddybuddy.tmp").exists())

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
                    "adapt_caddyfile_to_json",
                    new=AsyncMock(return_value={"apps": {}}),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config",
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
                    "load_config",
                    new=AsyncMock(return_value=None),
                ) as load_config,
            ):
                result = await caddyfile_manager.sync_caddy_configuration(session)
                await session.commit()

        self.assertEqual(result.status, "no_change")
        self.assertFalse(result.synced)
        load_config.assert_not_awaited()

    async def test_sync_loads_new_config_when_sqlite_changes(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text('example.com {\n    respond "ok" 200\n}\n', encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "adapt_caddyfile_to_json",
                    new=AsyncMock(return_value={"apps": {}}),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config",
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
                    "adapt_caddyfile_to_json",
                    new=AsyncMock(return_value={"apps": {"http": {}}}),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config",
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

    async def test_sync_reports_admin_api_timeout(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text('example.com {\n    respond "ok" 200\n}\n', encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "adapt_caddyfile_to_json",
                    new=AsyncMock(return_value={"apps": {}}),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config",
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
                    "adapt_caddyfile_to_json",
                    new=AsyncMock(return_value={"apps": {}}),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config",
                    new=AsyncMock(side_effect=CaddyServiceError("Caddy Admin API request timed out.")),
                ),
            ):
                result = await caddyfile_manager.sync_caddy_configuration(session)
                await session.commit()

        self.assertEqual(result.status, "sync_failed")
        self.assertEqual(result.error_code, "caddy_admin_unavailable")
        self.assertIn("timed out", result.error)

    async def test_sync_reports_admin_api_http_error(self) -> None:
        caddyfile_path = self.temp_path / "Caddyfile"
        caddyfile_path.write_text('example.com {\n    respond "ok" 200\n}\n', encoding="utf-8")

        async with self.session_factory() as session:
            with (
                patch.object(caddyfile_manager, "get_settings", return_value=self._settings(caddyfile_path)),
                patch.object(caddyfile_manager, "_admin_api_reachable", new=AsyncMock(return_value=True)),
                patch.object(
                    caddyfile_manager.caddy_service,
                    "adapt_caddyfile_to_json",
                    new=AsyncMock(return_value={"apps": {}}),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config",
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
                    "adapt_caddyfile_to_json",
                    new=AsyncMock(return_value={"apps": {}}),
                ),
                patch.object(
                    caddyfile_manager.CaddyAdminClient,
                    "load_config",
                    new=AsyncMock(side_effect=CaddyServiceError("Caddy Admin API request failed with status 500.")),
                ),
            ):
                result = await caddyfile_manager.sync_caddy_configuration(session)
                await session.commit()

        self.assertEqual(result.status, "sync_failed")
        self.assertEqual(result.error_code, "caddy_admin_unavailable")
        self.assertIn("status 500", result.error)

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
                "adapt_caddyfile_to_json",
                new=AsyncMock(return_value={"apps": {}}),
            ),
            patch.object(
                caddyfile_manager.CaddyAdminClient,
                "load_config",
                new=AsyncMock(return_value=None),
            ) as load_config,
        ):
            first_status, second_status = await asyncio.gather(run_onboarding(), run_onboarding())

        async with self.session_factory() as session:
            snapshot_count = await self._snapshot_count(session)

        load_config.assert_awaited_once()
        self.assertEqual(snapshot_count, 1)
        self.assertEqual({first_status, second_status}, {"onboarded", "already_managed"})


if __name__ == "__main__":
    unittest.main()