#!/usr/bin/env python3
#
# tests/test_caddy_onboarding.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

_ENV_OVERRIDES = {
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CADDYBUDDY_ADMIN_PASSWORD": "unit-test-password",
}
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}

for key, value in _ENV_OVERRIDES.items():
    os.environ[key] = value

from app.config.settings import get_settings
from app.models.base import Base
from app.services.caddy_onboarding import (
    OnboardingWizardState,
    _caddyfile_atomically_replaceable_sync,
    _enable_admin_in_caddyfile_sync,
    _prepare_default_config_sync,
    _render_default_config,
    _rollback_prepared_default_config_sync,
    _restore_caddyfile_sync,
    enable_admin_api_and_reprobe,
    execute_onboarding,
    get_onboarding_state,
    lock_onboarding_state,
    reset_onboarding_state,
    run_onboarding_preflight,
    save_onboarding_state,
    start_onboarding,
)
from app.services.runtime_settings import (
    get_caddy_config,
    get_ssllabs_email,
    set_caddy_api_url,
    set_caddyfile_path,
    set_ssllabs_email,
)


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


class CaddyOnboardingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        for key, value in _ENV_OVERRIDES.items():
            os.environ[key] = value
        get_settings.cache_clear()
        self._temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self._temp_dir.name) / "onboarding.db"
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{database_path}",
            connect_args={"timeout": 30},
            poolclass=NullPool,
        )

        @event.listens_for(self.engine.sync_engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=30000")
            finally:
                cursor.close()

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._temp_dir.cleanup()

    async def test_start_onboarding_persists_selected_mode(self) -> None:
        async with self.session_factory() as session:
            state = await start_onboarding(session, mode="docker")
            await session.commit()

        self.assertEqual(state.status, "in_progress")
        self.assertEqual(state.mode, "docker")

        async with self.session_factory() as session:
            persisted = await get_onboarding_state(session)

        self.assertEqual(persisted.mode, "docker")
        self.assertEqual(persisted.status, "in_progress")

    async def test_start_onboarding_persists_runtime_location_and_suggested_path(self) -> None:
        async with self.session_factory() as session:
            with patch(
                "app.services.caddy_onboarding.suggest_caddyfile_path",
                return_value="/etc/caddy/Caddyfile",
            ):
                state = await start_onboarding(session, mode="host", runtime_location="host")
                await session.commit()

        self.assertEqual(state.runtime_location, "host")
        self.assertEqual(state.caddyfile_path, "/etc/caddy/Caddyfile")

        async with self.session_factory() as session:
            persisted = await get_onboarding_state(session)

        self.assertEqual(persisted.runtime_location, "host")
        self.assertEqual(persisted.caddyfile_path, "/etc/caddy/Caddyfile")

    async def test_start_onboarding_auto_detects_when_runtime_location_blank(self) -> None:
        """A blank runtime_location must auto-detect, never raise the removed question's error."""
        for blank in ("", "   ", None):
            with self.subTest(blank=blank):
                async with self.session_factory() as session:
                    with patch(
                        "app.services.caddy_onboarding.detect_runtime_location",
                        return_value="container",
                    ):
                        state = await start_onboarding(session, mode="docker", runtime_location=blank)
                        await session.commit()

                self.assertEqual(state.status, "in_progress")
                self.assertEqual(state.runtime_location, "container")

    async def test_start_onboarding_resets_stale_preflight_state(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )
                await session.commit()

        self.assertTrue(state.preflight_ok)

        async with self.session_factory() as session:
            reset_state = await start_onboarding(session, mode="missing")
            await session.commit()

        self.assertEqual(reset_state.status, "in_progress")
        self.assertEqual(reset_state.mode, "missing")
        self.assertIsNone(reset_state.last_preflight_at)
        self.assertIsNone(reset_state.caddy_version)
        self.assertFalse(reset_state.admin_api_reachable)
        self.assertFalse(reset_state.caddyfile_writable)
        self.assertFalse(reset_state.api_only_takeover)
        self.assertEqual(reset_state.preflight_errors, [])
        self.assertEqual(reset_state.preflight_warnings, [])
        self.assertEqual(reset_state.field_errors, {})
        self.assertFalse(reset_state.preflight_ok)

    async def test_start_onboarding_rejects_unknown_mode(self) -> None:
        async with self.session_factory() as session:
            with self.assertRaisesRegex(ValueError, "supported Caddy onboarding situation"):
                await start_onboarding(session, mode="systemd")

    async def test_start_onboarding_rejects_completed_onboarding(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
                patch("app.services.caddy_onboarding.set_caddy_api_url", new=AsyncMock()),
                patch("app.services.caddy_onboarding.set_caddyfile_path", new=AsyncMock()),
                patch("app.services.caddy_onboarding.set_ssllabs_email", new=AsyncMock()),
                patch(
                    "app.services.caddy_onboarding.onboard_caddy",
                    new=AsyncMock(return_value=SimpleNamespace(status="onboarded", error=None)),
                ),
            ):
                await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )
                await execute_onboarding(session, exclusive_manager_confirmed=True)
                await session.commit()

        async with self.session_factory() as session:
            with self.assertRaisesRegex(ValueError, "already completed"):
                await start_onboarding(session, mode="docker")

    async def test_reset_onboarding_state_clears_progress(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            await session.commit()

        async with self.session_factory() as session:
            state = await reset_onboarding_state(session)
            await session.commit()

        self.assertEqual(state.status, "not_started")
        self.assertIsNone(state.mode)
        self.assertEqual(state.runtime_location, "")

        async with self.session_factory() as session:
            persisted = await get_onboarding_state(session)

        self.assertEqual(persisted.status, "not_started")
        self.assertIsNone(persisted.mode)

    async def test_missing_caddy_mode_passes_preflight_with_warnings(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="missing")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(False, None, False, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(False, False, "Caddyfile path does not exist yet.")),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

        self.assertEqual(state.status, "in_progress")
        self.assertTrue(state.preflight_ok)
        self.assertEqual(state.preflight_errors, [])
        self.assertIn("Admin API and version check are skipped", "; ".join(state.preflight_warnings))
        self.assertIn("Caddyfile path does not exist yet", "; ".join(state.preflight_warnings))

    async def test_preflight_rejects_non_caddy_2_runtime(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v1.0.5", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

        self.assertEqual(state.status, "failed")
        self.assertFalse(state.preflight_ok)
        self.assertIn("Caddy 2.x", "; ".join(state.preflight_errors))

    async def test_preflight_accepts_reachable_admin_api_without_version_string(self) -> None:
        """Caddy's admin API has no version endpoint (GET / is 404), so a reachable API with a
        readable JSON config must pass preflight even when no version string is detected."""
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, None, True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

        self.assertTrue(state.preflight_ok, "; ".join(state.preflight_errors))
        self.assertIsNone(state.caddy_version)
        self.assertNotIn("could not be detected", "; ".join(state.preflight_errors))

    async def test_successful_preflight_persists_detected_runtime_details(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="Admin@Example.COM",
                    caddyfile_path="/app/Caddyfile",
                )
                await session.commit()

        async with self.session_factory() as session:
            persisted = await get_onboarding_state(session)

        self.assertTrue(state.preflight_ok)
        self.assertEqual(state.status, "in_progress")
        self.assertEqual(state.caddy_version, "v2.8.4")
        self.assertEqual(state.acme_email, "admin@example.com")
        self.assertEqual(
            state.field_check_statuses,
            {
                "admin_api_url": "passed",
                "acme_email": "passed",
                "caddyfile_path": "passed",
            },
        )
        self.assertEqual(
            state.field_check_values,
            {
                "admin_api_url": "http://localhost:2019",
                "acme_email": "admin@example.com",
                "caddyfile_path": "/app/Caddyfile",
            },
        )
        self.assertTrue(state.admin_config_readable)
        self.assertTrue(state.caddyfile_writable)
        self.assertEqual(persisted.status, "in_progress")
        self.assertEqual(persisted.caddy_version, "v2.8.4")
        self.assertEqual(persisted.acme_email, "admin@example.com")
        self.assertEqual(persisted.field_check_statuses, state.field_check_statuses)
        self.assertEqual(persisted.field_check_values, state.field_check_values)
        self.assertTrue(persisted.admin_config_readable)
        self.assertTrue(persisted.caddyfile_writable)

    async def test_default_config_preflight_requires_existing_default_caddyfile(self) -> None:
        missing_default = Path(self._temp_dir.name) / "missing-default.Caddyfile"

        async with self.session_factory() as session:
            await start_onboarding(session, mode="default_config")
            with (
                patch("app.services.caddy_onboarding._default_caddyfile_path", return_value=missing_default),
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(False, True, None)),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

        self.assertEqual(state.status, "failed")
        self.assertFalse(state.preflight_ok)
        self.assertIn("/opt/caddybuddy/Caddyfile", "; ".join(state.preflight_errors))

    async def test_default_config_preflight_requires_valid_acme_email(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="default_config")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
                patch("app.services.caddy_onboarding._read_default_config_sync", return_value=":443 {\n}\n"),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="not-an-email",
                    caddyfile_path="/app/Caddyfile",
                )

        self.assertEqual(state.status, "failed")
        self.assertFalse(state.preflight_ok)
        self.assertIn("email", "; ".join(state.preflight_errors).lower())
        self.assertIn("acme_email", state.field_errors)

    async def test_preflight_requires_acme_email_for_default_config_mode(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="default_config")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
                patch("app.services.caddy_onboarding._read_default_config_sync", return_value=":443 {\n}\n"),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="",
                    caddyfile_path="/app/Caddyfile",
                )

        self.assertEqual(state.status, "failed")
        self.assertFalse(state.preflight_ok)
        self.assertIn("ACME/TLS email is required", "; ".join(state.preflight_errors))

    async def test_preflight_requires_acme_email_for_host_mode(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="",
                    caddyfile_path="/app/Caddyfile",
                )

        self.assertEqual(state.status, "failed")
        self.assertFalse(state.preflight_ok)
        self.assertIn("ACME/TLS email is required", "; ".join(state.preflight_errors))

    async def test_public_admin_api_url_fails_without_network_probe(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            probe_admin_api = AsyncMock()

            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=probe_admin_api),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://8.8.8.8:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

        self.assertEqual(state.status, "failed")
        self.assertIn("caddy_api_url host is not allowed", "; ".join(state.preflight_errors))
        self.assertIn("admin_api_url", state.field_errors)
        probe_admin_api.assert_not_awaited()

    async def test_host_preflight_unreachable_api_fails_with_actionable_guidance(self) -> None:
        """A host with Caddy running but the Admin API disabled must fail preflight (not execution)."""
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch(
                    "app.services.caddy_onboarding._probe_admin_api",
                    new=AsyncMock(return_value=(False, None, False, "Connection refused")),
                ),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/etc/caddy/Caddyfile",
                )

        self.assertEqual(state.status, "failed")
        self.assertFalse(state.preflight_passed, "Unreachable API must not mark preflight as passed")
        self.assertFalse(state.preflight_ok)
        self.assertIn("admin_api_url", state.field_errors)
        self.assertEqual(state.field_check_statuses.get("admin_api_url"), "failed")
        self.assertEqual(state.field_check_statuses.get("acme_email"), "passed")
        self.assertEqual(state.field_check_statuses.get("caddyfile_path"), "passed")
        joined = "; ".join(state.preflight_errors)
        self.assertIn("Caddy Admin API is not reachable from CaddyBuddy.", joined)
        # Chicken-and-egg guidance: a fresh Caddy commonly ships with the admin endpoint disabled.
        self.assertIn("localhost:2019", joined)
        self.assertIn("admin off", joined)

    async def test_successful_preflight_marks_preflight_passed(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch(
                    "app.services.caddy_onboarding._probe_admin_api",
                    new=AsyncMock(return_value=(True, "v2.8.4", True, None)),
                ),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/etc/caddy/Caddyfile",
                )

        self.assertEqual(state.status, "in_progress")
        self.assertTrue(state.preflight_passed)
        self.assertTrue(state.preflight_ok)

    # ------------------------------------------------------------------ assisted Admin-API enable

    def _write_caddyfile(self, content: str) -> str:
        path = Path(self._temp_dir.name) / "Caddyfile"
        path.write_text(content, encoding="utf-8")
        return str(path)

    @staticmethod
    def _allow_temp_caddyfile():
        # The temp dir is outside the production Caddyfile allowlist; bypass path normalization
        # so these tests can exercise the real file transform/restore logic on a temp file.
        return patch("app.services.caddy_onboarding.normalize_caddyfile_path", side_effect=lambda p: p)

    @staticmethod
    def _fake_supervisor(restart_results):
        return SimpleNamespace(restart=AsyncMock(side_effect=list(restart_results)))

    @staticmethod
    def _restart_ok():
        return SimpleNamespace(success=True, output="", error=None)

    @staticmethod
    def _restart_fail(error="boom"):
        return SimpleNamespace(success=False, output="", error=error)

    async def _seed_assist_state(self, *, caddyfile_path: str, mode: str = "host") -> None:
        async with self.session_factory() as session:
            state = OnboardingWizardState(
                status="failed",
                mode=mode,
                runtime_location="host",
                admin_api_url="http://localhost:2019",
                caddyfile_path=caddyfile_path,
                acme_email="admin@example.com",
                last_preflight_at="2026-06-13T12:00:00+00:00",
                preflight_passed=False,
                caddyfile_writable=True,
                admin_api_assist_available=True,
            )
            await save_onboarding_state(session, state)
            await session.commit()

    # ---- pure helper: exactly one managed admin directive, email preserved --------------------

    def test_enable_admin_helper_creates_global_block_when_missing(self) -> None:
        path = self._write_caddyfile(":443 {\n}\n")
        with self._allow_temp_caddyfile():
            _enable_admin_in_caddyfile_sync(path, "localhost:2019")
        result = Path(path).read_text(encoding="utf-8")
        self.assertEqual(result.count("admin "), 1)
        self.assertIn("admin localhost:2019", result)
        self.assertIn(":443 {", result)

    def test_enable_admin_helper_replaces_admin_off(self) -> None:
        path = self._write_caddyfile("{\n    admin off\n}\n\n:443 {\n}\n")
        with self._allow_temp_caddyfile():
            _enable_admin_in_caddyfile_sync(path, "localhost:2019")
        result = Path(path).read_text(encoding="utf-8")
        self.assertNotIn("admin off", result)
        self.assertEqual(len(re.findall(r"(?m)^\s*admin\b", result)), 1)
        self.assertIn("admin localhost:2019", result)

    def test_enable_admin_helper_replaces_other_admin_bind(self) -> None:
        path = self._write_caddyfile("{\n    admin :2020\n}\n")
        with self._allow_temp_caddyfile():
            _enable_admin_in_caddyfile_sync(path, "localhost:2019")
        result = Path(path).read_text(encoding="utf-8")
        self.assertNotIn("admin :2020", result)
        self.assertEqual(len(re.findall(r"(?m)^\s*admin\b", result)), 1)

    def test_enable_admin_helper_preserves_email_and_is_idempotent(self) -> None:
        path = self._write_caddyfile("{\n    email ops@example.com\n    admin off\n}\n")
        with self._allow_temp_caddyfile():
            backup = _enable_admin_in_caddyfile_sync(path, "localhost:2019")
            first = Path(path).read_text(encoding="utf-8")
            self.assertIn("email ops@example.com", first, "User's ACME email must be preserved")
            self.assertEqual(len(re.findall(r"(?m)^\s*admin\b", first)), 1)
            self.assertTrue(Path(backup).is_file(), "A backup must be written")
            # Idempotent: running again yields the same managed result.
            _enable_admin_in_caddyfile_sync(path, "localhost:2019")
        second = Path(path).read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"(?m)^\s*admin\b", second)), 1)
        self.assertIn("email ops@example.com", second)

    def test_enable_admin_helper_preserves_file_mode(self) -> None:
        path = self._write_caddyfile("{\n}\n")
        os.chmod(path, 0o640)
        with self._allow_temp_caddyfile():
            _enable_admin_in_caddyfile_sync(path, "localhost:2019")
        self.assertEqual(Path(path).stat().st_mode & 0o777, 0o640)

    def test_prepare_default_config_preserves_existing_file_mode(self) -> None:
        default_config = Path(self._temp_dir.name) / "Default.Caddyfile"
        default_config.write_text("{\n    email admin@example.com\n}\n", encoding="utf-8")
        target_config = Path(self._temp_dir.name) / "Caddyfile"
        target_config.write_text("old config\n", encoding="utf-8")
        os.chmod(target_config, 0o640)

        with (
            patch("app.services.caddy_onboarding._default_caddyfile_path", return_value=default_config),
            self._allow_temp_caddyfile(),
        ):
            _prepare_default_config_sync(
                str(target_config),
                acme_email="admin@example.com",
                admin_api_url="http://localhost:2019",
            )

        self.assertEqual(target_config.stat().st_mode & 0o777, 0o640)
        self.assertEqual(
            target_config.read_text(encoding="utf-8"),
            default_config.read_text(encoding="utf-8"),
        )

    def test_rollback_prepared_default_config_restores_original_mode(self) -> None:
        default_config = Path(self._temp_dir.name) / "Default.Caddyfile"
        default_config.write_text("{\n    email admin@example.com\n}\n", encoding="utf-8")
        target_config = Path(self._temp_dir.name) / "Caddyfile"
        original_content = "old config\n"
        target_config.write_text(original_content, encoding="utf-8")
        os.chmod(target_config, 0o640)

        with (
            patch("app.services.caddy_onboarding._default_caddyfile_path", return_value=default_config),
            self._allow_temp_caddyfile(),
        ):
            backup_path = _prepare_default_config_sync(
                str(target_config),
                acme_email="admin@example.com",
                admin_api_url="http://localhost:2019",
            )
            self.assertIsNotNone(backup_path)
            os.chmod(Path(backup_path), 0o600)
            _rollback_prepared_default_config_sync(str(target_config), backup_path)

        self.assertEqual(target_config.read_text(encoding="utf-8"), original_content)
        self.assertEqual(target_config.stat().st_mode & 0o777, 0o640)

    # ---- availability flag in preflight -------------------------------------------------------

    async def _run_assist_preflight(self, *, mode="host", control_mode="systemd",
                                    probe=(False, None, False, None),
                                    inspect=(True, True, None), replaceable=(True, None),
                                    acme_email="admin@example.com"):
        async with self.session_factory() as session:
            await start_onboarding(session, mode=mode)
            with (
                patch.dict(os.environ, {"CB_CADDY_CONTROL_MODE": control_mode}),
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=probe)),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=inspect),
                patch("app.services.caddy_onboarding._caddyfile_atomically_replaceable_sync",
                      return_value=replaceable),
            ):
                get_settings.cache_clear()
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email=acme_email,
                    caddyfile_path="/etc/caddy/Caddyfile",
                )
        get_settings.cache_clear()
        return state

    async def test_assist_available_when_api_is_sole_blocker(self) -> None:
        state = await self._run_assist_preflight()
        self.assertTrue(state.admin_api_assist_available)

    async def test_assist_unavailable_when_control_mode_disabled(self) -> None:
        state = await self._run_assist_preflight(control_mode="disabled")
        self.assertFalse(state.admin_api_assist_available)

    async def test_assist_unavailable_when_caddyfile_not_writable(self) -> None:
        state = await self._run_assist_preflight(inspect=(True, False, "Caddyfile path is readable but not writable."))
        self.assertFalse(state.admin_api_assist_available)

    async def test_assist_unavailable_when_not_replaceable(self) -> None:
        state = await self._run_assist_preflight(
            replaceable=(False, "Caddyfile parent directory is not writable.")
        )
        self.assertFalse(state.admin_api_assist_available)

    async def test_assist_unavailable_for_unsupported_mode(self) -> None:
        state = await self._run_assist_preflight(mode="docker")
        self.assertFalse(state.admin_api_assist_available)

    async def test_assist_unavailable_when_other_field_also_errors(self) -> None:
        # A bad ACME email adds an acme_email field error -> the API is no longer the sole blocker.
        state = await self._run_assist_preflight(acme_email="not-an-email")
        self.assertFalse(state.admin_api_assist_available)

    # ---- enable_admin_api_and_reprobe ---------------------------------------------------------

    async def test_enable_admin_api_success(self) -> None:
        path = self._write_caddyfile("{\n}\n\n:443 {\n}\n")
        await self._seed_assist_state(caddyfile_path=path)

        probe_calls = {"n": 0}

        async def fake_probe(_url):
            probe_calls["n"] += 1
            if probe_calls["n"] == 1:
                return (False, None, False, None)  # shortcut check: still unreachable
            return (True, "v2.8.4", True, None)    # reachable after restart

        async with self.session_factory() as session:
            with (
                self._allow_temp_caddyfile(),
                patch.dict(os.environ, {"CB_CADDY_CONTROL_MODE": "systemd"}),
                patch("app.services.caddy_onboarding.get_caddy_supervisor",
                      new=AsyncMock(return_value=self._fake_supervisor([self._restart_ok()]))),
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(side_effect=fake_probe)),
                patch("app.services.caddy_onboarding.asyncio.sleep", new=AsyncMock()),
            ):
                get_settings.cache_clear()
                state = await enable_admin_api_and_reprobe(session)
                await session.commit()
        get_settings.cache_clear()

        self.assertTrue(state.preflight_passed)
        self.assertFalse(state.admin_api_assist_available, "Success must clear the assist flag")
        content = Path(path).read_text(encoding="utf-8")
        self.assertIn("admin localhost:2019", content)
        backups = list(Path(self._temp_dir.name).glob("Caddyfile.caddybuddy-backup-*"))
        self.assertTrue(backups, "A backup must have been created")

    async def test_enable_admin_api_shortcut_when_already_reachable(self) -> None:
        path = self._write_caddyfile("{\n}\n")
        original = Path(path).read_text(encoding="utf-8")
        await self._seed_assist_state(caddyfile_path=path)
        supervisor = self._fake_supervisor([self._restart_ok()])

        async with self.session_factory() as session:
            with (
                self._allow_temp_caddyfile(),
                patch.dict(os.environ, {"CB_CADDY_CONTROL_MODE": "systemd"}),
                patch("app.services.caddy_onboarding.get_caddy_supervisor", new=AsyncMock(return_value=supervisor)),
                patch("app.services.caddy_onboarding._probe_admin_api",
                      new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
            ):
                get_settings.cache_clear()
                state = await enable_admin_api_and_reprobe(session)
                await session.commit()
        get_settings.cache_clear()

        supervisor.restart.assert_not_awaited()
        self.assertEqual(Path(path).read_text(encoding="utf-8"), original, "File must be untouched")
        self.assertTrue(state.preflight_passed)

    async def test_enable_admin_api_restart_failure_rolls_back(self) -> None:
        path = self._write_caddyfile("{\n    admin off\n}\n")
        original = Path(path).read_text(encoding="utf-8")
        await self._seed_assist_state(caddyfile_path=path)
        # First restart (enable) fails; second restart (after rollback) succeeds.
        supervisor = self._fake_supervisor([self._restart_fail("restart failed"), self._restart_ok()])

        async with self.session_factory() as session:
            with (
                self._allow_temp_caddyfile(),
                patch.dict(os.environ, {"CB_CADDY_CONTROL_MODE": "systemd"}),
                patch("app.services.caddy_onboarding.get_caddy_supervisor", new=AsyncMock(return_value=supervisor)),
                patch("app.services.caddy_onboarding._probe_admin_api",
                      new=AsyncMock(return_value=(False, None, False, None))),
                patch("app.services.caddy_onboarding.asyncio.sleep", new=AsyncMock()),
            ):
                get_settings.cache_clear()
                state = await enable_admin_api_and_reprobe(session)
                await session.commit()
        get_settings.cache_clear()

        self.assertEqual(state.status, "failed")
        self.assertEqual(Path(path).read_text(encoding="utf-8"), original, "Original bytes must be restored")
        self.assertIn("restart", (state.error_message or "").lower())

    async def test_enable_admin_api_unreachable_after_restart_rolls_back(self) -> None:
        path = self._write_caddyfile("{\n    admin off\n}\n")
        original = Path(path).read_text(encoding="utf-8")
        await self._seed_assist_state(caddyfile_path=path)
        # Enable restart ok, rollback restart fails -> message about restart-after-rollback.
        supervisor = self._fake_supervisor([self._restart_ok(), self._restart_fail("down")])

        async with self.session_factory() as session:
            with (
                self._allow_temp_caddyfile(),
                patch.dict(os.environ, {"CB_CADDY_CONTROL_MODE": "systemd"}),
                patch("app.services.caddy_onboarding.get_caddy_supervisor", new=AsyncMock(return_value=supervisor)),
                patch("app.services.caddy_onboarding._probe_admin_api",
                      new=AsyncMock(return_value=(False, None, False, None))),
                patch("app.services.caddy_onboarding.asyncio.sleep", new=AsyncMock()),
            ):
                get_settings.cache_clear()
                state = await enable_admin_api_and_reprobe(session)
                await session.commit()
        get_settings.cache_clear()

        self.assertEqual(state.status, "failed")
        self.assertEqual(Path(path).read_text(encoding="utf-8"), original)
        self.assertIn("still unreachable", (state.error_message or "").lower())
        self.assertIn("could not be restarted", (state.error_message or "").lower())
        self.assertIsNotNone(state.backup_path)

    async def test_enable_admin_api_rollback_restore_failure_retains_backup(self) -> None:
        path = self._write_caddyfile("{\n    admin off\n}\n")
        await self._seed_assist_state(caddyfile_path=path)
        supervisor = self._fake_supervisor([self._restart_fail("restart failed")])

        async with self.session_factory() as session:
            with (
                self._allow_temp_caddyfile(),
                patch.dict(os.environ, {"CB_CADDY_CONTROL_MODE": "systemd"}),
                patch("app.services.caddy_onboarding.get_caddy_supervisor", new=AsyncMock(return_value=supervisor)),
                patch("app.services.caddy_onboarding._probe_admin_api",
                      new=AsyncMock(return_value=(False, None, False, None))),
                patch("app.services.caddy_onboarding._restore_caddyfile_sync",
                      side_effect=ValueError("restore failed")),
                patch("app.services.caddy_onboarding.asyncio.sleep", new=AsyncMock()),
            ):
                get_settings.cache_clear()
                state = await enable_admin_api_and_reprobe(session)
                await session.commit()
        get_settings.cache_clear()

        self.assertEqual(state.status, "failed")
        self.assertIn("manual recovery", (state.error_message or "").lower())
        self.assertIsNotNone(state.backup_path)

    async def test_enable_admin_api_unexpected_error_after_change_rolls_back(self) -> None:
        path = self._write_caddyfile("{\n    admin off\n}\n")
        original = Path(path).read_text(encoding="utf-8")
        await self._seed_assist_state(caddyfile_path=path)
        supervisor = self._fake_supervisor([self._restart_ok(), self._restart_ok()])

        probe_calls = {"n": 0}

        async def fake_probe(_url):
            probe_calls["n"] += 1
            if probe_calls["n"] == 1:
                return (False, None, False, None)  # shortcut: unreachable
            raise RuntimeError("probe crashed")    # after restart

        async with self.session_factory() as session:
            with (
                self._allow_temp_caddyfile(),
                patch.dict(os.environ, {"CB_CADDY_CONTROL_MODE": "systemd"}),
                patch("app.services.caddy_onboarding.get_caddy_supervisor", new=AsyncMock(return_value=supervisor)),
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(side_effect=fake_probe)),
                patch("app.services.caddy_onboarding.asyncio.sleep", new=AsyncMock()),
            ):
                get_settings.cache_clear()
                state = await enable_admin_api_and_reprobe(session)
                await session.commit()
        get_settings.cache_clear()

        self.assertEqual(state.status, "failed")
        self.assertEqual(Path(path).read_text(encoding="utf-8"), original)
        self.assertIn("failed to enable", (state.error_message or "").lower())

    async def test_enable_admin_api_rejects_disabled_control_mode(self) -> None:
        path = self._write_caddyfile("{\n}\n")
        await self._seed_assist_state(caddyfile_path=path)
        async with self.session_factory() as session:
            with patch.dict(os.environ, {"CB_CADDY_CONTROL_MODE": "disabled"}):
                get_settings.cache_clear()
                with self.assertRaisesRegex(ValueError, "restart capability is not configured"):
                    await enable_admin_api_and_reprobe(session)
        get_settings.cache_clear()

    async def test_enable_admin_api_rejects_misconfigured_supervisor(self) -> None:
        path = self._write_caddyfile("{\n}\n")
        await self._seed_assist_state(caddyfile_path=path)
        async with self.session_factory() as session:
            with (
                patch.dict(os.environ, {"CB_CADDY_CONTROL_MODE": "systemd"}),
                patch("app.services.caddy_onboarding.get_caddy_supervisor",
                      new=AsyncMock(side_effect=ValueError("Invalid systemd unit name"))),
            ):
                get_settings.cache_clear()
                with self.assertRaisesRegex(ValueError, "misconfigured"):
                    await enable_admin_api_and_reprobe(session)
        get_settings.cache_clear()

    async def test_enable_admin_api_rejects_non_loopback_bind_host(self) -> None:
        path = self._write_caddyfile("{\n    admin off\n}\n")
        async with self.session_factory() as session:
            state = OnboardingWizardState(
                status="failed",
                mode="host",
                runtime_location="host",
                admin_api_url="http://10.0.0.8:2019",
                caddyfile_path=path,
                acme_email="admin@example.com",
                last_preflight_at="2026-06-13T12:00:00+00:00",
                preflight_passed=False,
                caddyfile_writable=True,
                admin_api_assist_available=True,
            )
            await save_onboarding_state(session, state)
            await session.commit()

        async with self.session_factory() as session:
            with (
                self._allow_temp_caddyfile(),
                patch.dict(os.environ, {"CB_CADDY_CONTROL_MODE": "systemd"}),
                patch("app.services.caddy_onboarding.get_caddy_supervisor",
                      new=AsyncMock(return_value=self._fake_supervisor([self._restart_ok()]))),
            ):
                get_settings.cache_clear()
                with self.assertRaisesRegex(ValueError, "only bind Caddy Admin API to localhost"):
                    await enable_admin_api_and_reprobe(session)
        get_settings.cache_clear()

    async def test_invalid_admin_api_url_fails_without_network_probe(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            probe_admin_api = AsyncMock()

            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=probe_admin_api),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="not-a-url",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

        self.assertEqual(state.status, "failed")
        self.assertFalse(state.preflight_ok)
        self.assertIn("caddy_api_url", "; ".join(state.preflight_errors).lower())
        probe_admin_api.assert_not_awaited()

    async def test_docker_preflight_records_unmounted_caddyfile_limitation(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="docker")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch(
                    "app.services.caddy_onboarding._inspect_caddyfile_path",
                    return_value=(False, False, "Caddyfile path is not mounted into this container."),
                ),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://host.docker.internal:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/etc/caddy/Caddyfile",
                )

        self.assertEqual(state.mode, "docker")
        self.assertTrue(state.preflight_ok)
        self.assertTrue(state.admin_config_readable)
        self.assertFalse(state.caddyfile_writable)
        self.assertIn("mounted", "; ".join(state.preflight_warnings))
        self.assertTrue(state.api_only_takeover)

    async def test_existing_config_preflight_requires_readable_and_writable_takeover_paths(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="existing_config")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", False, None))),
                patch(
                    "app.services.caddy_onboarding._inspect_caddyfile_path",
                    return_value=(True, False, "Caddyfile path is readable but not writable."),
                ),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

        self.assertEqual(state.status, "failed")
        self.assertIn("readable through the Admin API", "; ".join(state.preflight_errors))
        self.assertIn("must be writable", "; ".join(state.preflight_errors))

    async def test_execute_persists_completed_state(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
                patch("app.services.caddy_onboarding.set_caddy_api_url", new=AsyncMock()),
                patch("app.services.caddy_onboarding.set_caddyfile_path", new=AsyncMock()),
                patch("app.services.caddy_onboarding.set_ssllabs_email", new=AsyncMock()),
                patch(
                    "app.services.caddy_onboarding.onboard_caddy",
                    new=AsyncMock(return_value=SimpleNamespace(status="onboarded", error=None)),
                ),
            ):
                state = await execute_onboarding(session, exclusive_manager_confirmed=True)
                await session.commit()

        async with self.session_factory() as session:
            persisted = await get_onboarding_state(session)

        self.assertEqual(state.status, "completed")
        self.assertTrue(state.exclusive_manager_confirmed)
        self.assertIsNotNone(state.completed_at)
        self.assertEqual(persisted.status, "completed")
        self.assertTrue(persisted.exclusive_manager_confirmed)
        self.assertIsNotNone(persisted.completed_at)

    async def test_execute_missing_caddy_mode_saves_future_settings_without_admin_api_takeover(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="missing")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(False, None, False, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(False, False, "Caddyfile path does not exist yet.")),
            ):
                await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/etc/caddy/Caddyfile",
                )

            with (
                patch("app.services.caddy_onboarding.set_caddy_api_url", new=AsyncMock()) as set_caddy_api_url,
                patch("app.services.caddy_onboarding.set_caddyfile_path", new=AsyncMock()) as set_caddyfile_path,
                patch("app.services.caddy_onboarding.set_ssllabs_email", new=AsyncMock()) as set_ssllabs_email,
                patch("app.services.caddy_onboarding.onboard_caddy", new=AsyncMock()) as onboard_caddy,
            ):
                state = await execute_onboarding(session, exclusive_manager_confirmed=True)
                await session.commit()

        self.assertEqual(state.status, "completed")
        self.assertTrue(state.exclusive_manager_confirmed)
        set_caddy_api_url.assert_awaited_once()
        set_caddyfile_path.assert_awaited_once()
        set_ssllabs_email.assert_awaited_once()
        onboard_caddy.assert_not_awaited()

    async def test_execute_default_config_prepares_target_and_preserves_backup(self) -> None:
        default_config = Path(self._temp_dir.name) / "Default.Caddyfile"
        default_config.write_text("{\n    email admin@example.com\n}\n", encoding="utf-8")
        target_config = Path(self._temp_dir.name) / "Caddyfile"
        target_config.write_text("old config\n", encoding="utf-8")

        async with self.session_factory() as session:
            await start_onboarding(session, mode="default_config")
            with (
                patch("app.services.caddy_onboarding._default_caddyfile_path", return_value=default_config),
                patch("app.services.caddy_onboarding.normalize_caddyfile_path", return_value=str(target_config)),
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path=str(target_config),
                )

                with (
                    patch("app.services.caddy_onboarding.set_caddy_api_url", new=AsyncMock()),
                    patch("app.services.caddy_onboarding.set_caddyfile_path", new=AsyncMock()),
                    patch("app.services.caddy_onboarding.set_ssllabs_email", new=AsyncMock()),
                    patch(
                        "app.services.caddy_onboarding.onboard_caddy",
                        new=AsyncMock(return_value=SimpleNamespace(status="onboarded", error=None)),
                    ),
                ):
                    state = await execute_onboarding(session, exclusive_manager_confirmed=True)

        self.assertEqual(state.status, "completed")
        self.assertEqual(target_config.read_text(encoding="utf-8"), default_config.read_text(encoding="utf-8"))
        self.assertEqual(Path(state.backup_path).read_text(encoding="utf-8"), "old config\n")
        self.assertIn(".caddybuddy-backup-", Path(state.backup_path).name)

    async def test_execute_persists_failed_state_when_onboard_caddy_fails(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
                patch("app.services.caddy_onboarding.set_caddy_api_url", new=AsyncMock()),
                patch("app.services.caddy_onboarding.set_caddyfile_path", new=AsyncMock()),
                patch("app.services.caddy_onboarding.set_ssllabs_email", new=AsyncMock()),
                patch(
                    "app.services.caddy_onboarding.onboard_caddy",
                    new=AsyncMock(return_value=SimpleNamespace(status="error", error="Caddy runtime is not ready.")),
                ),
            ):
                state = await execute_onboarding(session, exclusive_manager_confirmed=True)
                await session.commit()

        async with self.session_factory() as session:
            persisted = await get_onboarding_state(session)

        self.assertEqual(state.status, "failed")
        self.assertIn("runtime", state.error_message.lower())
        self.assertEqual(persisted.status, "failed")
        self.assertIn("runtime", persisted.error_message.lower())

    async def test_execute_revalidates_preflight_before_side_effects(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(False, None, False, "Connection refused"))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
                patch("app.services.caddy_onboarding.set_caddy_api_url", new=AsyncMock()) as set_caddy_api_url,
                patch("app.services.caddy_onboarding.set_caddyfile_path", new=AsyncMock()) as set_caddyfile_path,
                patch("app.services.caddy_onboarding.set_ssllabs_email", new=AsyncMock()) as set_ssllabs_email,
                patch("app.services.caddy_onboarding.onboard_caddy", new=AsyncMock()) as onboard_caddy,
            ):
                state = await execute_onboarding(session, exclusive_manager_confirmed=True)
                await session.commit()

        self.assertEqual(state.status, "failed")
        self.assertFalse(state.preflight_ok)
        self.assertIn("Admin API is not reachable", state.error_message or "")
        set_caddy_api_url.assert_not_awaited()
        set_caddyfile_path.assert_not_awaited()
        set_ssllabs_email.assert_not_awaited()
        onboard_caddy.assert_not_awaited()

    async def test_execute_failure_restores_previous_runtime_settings(self) -> None:
        async with self.session_factory() as session:
            await set_caddy_api_url(session, "http://localhost:2019")
            await set_caddyfile_path(session, "/app/Caddyfile")
            await set_ssllabs_email(session, "old@example.com")
            await session.commit()

        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2020",
                    acme_email="admin@example.com",
                    caddyfile_path="/etc/caddy/Caddyfile",
                )

            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
                patch(
                    "app.services.caddy_onboarding.onboard_caddy",
                    new=AsyncMock(return_value=SimpleNamespace(status="failed", error="deploy failed")),
                ),
            ):
                state = await execute_onboarding(session, exclusive_manager_confirmed=True)
                await session.commit()

        self.assertEqual(state.status, "failed")

        async with self.session_factory() as session:
            config = await get_caddy_config(session)
            email = await get_ssllabs_email(session)

        self.assertEqual(config.admin_url, "http://localhost:2019")
        self.assertEqual(config.caddyfile_path_str, "/app/Caddyfile")
        self.assertEqual(email, "old@example.com")

    async def test_execute_restores_default_config_backup_when_onboard_caddy_fails(self) -> None:
        default_config = Path(self._temp_dir.name) / "Default.Caddyfile"
        default_config.write_text("{\n    email admin@example.com\n}\n", encoding="utf-8")
        target_config = Path(self._temp_dir.name) / "Caddyfile"
        original_content = "example.com {\n    respond \"old\"\n}\n"
        target_config.write_text(original_content, encoding="utf-8")

        async with self.session_factory() as session:
            await start_onboarding(session, mode="default_config")
            with (
                patch("app.services.caddy_onboarding._default_caddyfile_path", return_value=default_config),
                patch("app.services.caddy_onboarding.normalize_caddyfile_path", return_value=str(target_config)),
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path=str(target_config),
                )

                with (
                    patch("app.services.caddy_onboarding.set_caddy_api_url", new=AsyncMock()),
                    patch("app.services.caddy_onboarding.set_caddyfile_path", new=AsyncMock()),
                    patch("app.services.caddy_onboarding.set_ssllabs_email", new=AsyncMock()),
                    patch(
                        "app.services.caddy_onboarding.onboard_caddy",
                        new=AsyncMock(return_value=SimpleNamespace(status="error", error="Caddy runtime is not ready.")),
                    ),
                ):
                    state = await execute_onboarding(session, exclusive_manager_confirmed=True)
                    await session.commit()

        self.assertEqual(state.status, "failed")
        self.assertEqual(target_config.read_text(encoding="utf-8"), original_content)
        self.assertIsNotNone(state.backup_path)
        self.assertTrue(Path(state.backup_path).is_file())

    async def test_execute_persists_failed_state_when_setting_update_raises(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
                patch(
                    "app.services.caddy_onboarding.set_caddy_api_url",
                    new=AsyncMock(side_effect=ValueError("Caddy API URL cannot be empty")),
                ),
            ):
                state = await execute_onboarding(session, exclusive_manager_confirmed=True)
                await session.commit()

        async with self.session_factory() as session:
            persisted = await get_onboarding_state(session)

        self.assertEqual(state.status, "failed")
        self.assertIn("Caddy API URL cannot be empty", state.error_message)
        self.assertEqual(persisted.status, "failed")

    def test_render_default_config_substitutes_placeholders(self) -> None:
        template = "{\n    email {{ ACME_EMAIL }}\n}\n# api {{ CADDY_ADMIN_API_URL }}\n"
        result = _render_default_config(
            template,
            acme_email="admin@example.com",
            admin_api_url="http://localhost:2019",
        )
        self.assertEqual(result, "{\n    email admin@example.com\n}\n# api http://localhost:2019\n")

    def test_render_default_config_raises_on_unresolved_placeholder(self) -> None:
        template = "email {{ ACME_EMAIL }}\nother {{ UNKNOWN }}\n"
        with self.assertRaises(ValueError):
            _render_default_config(template, acme_email="admin@example.com", admin_api_url="http://localhost:2019")

    def test_render_default_config_skips_empty_email(self) -> None:
        template = "email {{ ACME_EMAIL }}\n"
        with self.assertRaises(ValueError):
            _render_default_config(template, acme_email="", admin_api_url="http://localhost:2019")

    def test_bundled_default_caddyfile_uses_central_runtime_log(self) -> None:
        bundled = (Path(__file__).resolve().parents[1] / "Caddyfile").read_text(encoding="utf-8")

        self.assertIn("output file /var/log/caddy/runtime.json", bundled)
        self.assertIn("roll_keep_for 168h", bundled)
        self.assertNotIn("(default_log)", bundled)

    async def test_host_preflight_does_not_set_api_only_takeover(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                state = await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

        self.assertFalse(state.api_only_takeover)

    async def test_execute_requires_exclusive_manager_confirmation(self) -> None:
        async with self.session_factory() as session:
            await start_onboarding(session, mode="host")
            with (
                patch("app.services.caddy_onboarding._probe_admin_api", new=AsyncMock(return_value=(True, "v2.8.4", True, None))),
                patch("app.services.caddy_onboarding._inspect_caddyfile_path", return_value=(True, True, None)),
            ):
                await run_onboarding_preflight(
                    session,
                    admin_api_url="http://localhost:2019",
                    acme_email="admin@example.com",
                    caddyfile_path="/app/Caddyfile",
                )

            with self.assertRaisesRegex(ValueError, "exclusive Caddy configuration manager"):
                await execute_onboarding(session, exclusive_manager_confirmed=False)

    async def test_lock_onboarding_state_uses_begin_immediate_for_sqlite(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self._bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
                self.execute = AsyncMock()
                self.get = AsyncMock(return_value=None)

            def get_bind(self):
                return self._bind

            def in_transaction(self) -> bool:
                return False

        session = FakeSession()

        state = await lock_onboarding_state(session)

        self.assertEqual(state.status, "not_started")
        session.execute.assert_awaited_once()
        session.get.assert_awaited_once_with(
            unittest.mock.ANY,
            unittest.mock.ANY,
            with_for_update=True,
        )


if __name__ == "__main__":
    unittest.main()
