#!/usr/bin/env python3
#
# tests/test_runtime_settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
import app.services.runtime_settings as runtime_settings
from app.services.runtime_settings import (
    SSLLABS_RETENTION_DEFAULT_DAYS,
    discover_caddyfile_candidates,
    get_caddy_config,
    get_rate_limit_enabled,
    get_ssllabs_email,
    get_ssllabs_history_retention_days,
    normalize_caddy_api_url,
    normalize_caddyfile_path,
    normalize_ssllabs_email,
    suggest_caddyfile_path,
    set_caddy_api_url,
    set_caddy_config,
    set_caddyfile_path,
    set_rate_limit_enabled,
    set_ssllabs_email,
    set_ssllabs_history_retention_days,
)


class RuntimeSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self._temp_dir.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
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

    async def test_get_caddy_config_returns_defaults_when_not_persisted(self) -> None:
        async with self.session_factory() as session:
            config = await get_caddy_config(session)

        self.assertEqual(config.admin_url, "http://localhost:2019")
        self.assertEqual(config.caddyfile_path, Path("/app/Caddyfile"))

    async def test_setters_normalize_and_persist_values(self) -> None:
        async with self.session_factory() as session:
            await set_caddy_api_url(session, " http://localhost:2019/ ")
            await set_caddyfile_path(session, " /etc/caddy/Caddyfile ")
            await session.commit()

        async with self.session_factory() as session:
            config = await get_caddy_config(session)

        self.assertEqual(config.admin_url, "http://localhost:2019")
        self.assertEqual(config.caddyfile_path, Path("/etc/caddy/Caddyfile"))

    async def test_set_caddy_api_url_rejects_credentials(self) -> None:
        async with self.session_factory() as session:
            with self.assertRaisesRegex(ValueError, "must not include username or password"):
                await set_caddy_api_url(session, "http://user:pass@localhost:2019")

    async def test_get_caddy_config_normalizes_existing_values(self) -> None:
        async with self.session_factory() as session:
            await set_caddy_api_url(session, "http://localhost:2019/")
            await set_caddyfile_path(session, " /etc/caddy/Caddyfile ")
            await session.commit()

        async with self.session_factory() as session:
            config = await get_caddy_config(session)

        self.assertEqual(config.admin_url, "http://localhost:2019")
        self.assertEqual(config.caddyfile_path, Path("/etc/caddy/Caddyfile"))

    def test_normalize_caddy_api_url_rejects_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not include a path"):
            normalize_caddy_api_url("http://localhost:2019/config/")

    def test_normalize_caddy_api_url_rejects_credentials(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not include username or password"):
            normalize_caddy_api_url("http://user:pass@localhost:2019")

    def test_normalize_caddyfile_path_requires_absolute_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be absolute"):
            normalize_caddyfile_path("relative/Caddyfile")

    def test_normalize_caddyfile_path_rejects_nul_byte(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid character"):
            normalize_caddyfile_path("/etc/caddy/Caddyfile\x00")

    def test_normalize_caddyfile_path_returns_canonical_path(self) -> None:
        self.assertEqual(
            normalize_caddyfile_path("/app/../app/Caddyfile"),
            "/app/Caddyfile",
        )

    def test_normalize_caddyfile_path_allows_common_linux_locations(self) -> None:
        self.assertEqual(
            normalize_caddyfile_path("/usr/local/etc/caddy/Caddyfile"),
            "/usr/local/etc/caddy/Caddyfile",
        )
        self.assertEqual(
            normalize_caddyfile_path("/etc/opt/caddy/Caddyfile"),
            "/etc/opt/caddy/Caddyfile",
        )

    def test_suggest_caddyfile_path_prefers_existing_host_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "etc" / "caddy" / "Caddyfile"
            candidate.parent.mkdir(parents=True)
            candidate.write_text("localhost\n", encoding="utf-8")
            with patch.object(
                runtime_settings,
                "_HOST_CADDYFILE_HINTS",
                (candidate,),
            ):
                suggestion = suggest_caddyfile_path("host")

        self.assertEqual(suggestion, str(candidate))

    def test_discover_caddyfile_candidates_prefers_container_mount_path(self) -> None:
        mounted_path = Path("/tmp/caddy/Caddyfile")
        with patch.object(
            runtime_settings,
            "_CONTAINER_CADDYFILE_HINTS",
            (Path("/app/Caddyfile"),),
        ):
            candidates = discover_caddyfile_candidates("container", mounted_caddyfile_path=mounted_path)

        self.assertEqual(candidates[0], mounted_path)

    def test_normalize_caddy_api_url_rejects_public_hosts(self) -> None:
        with self.assertRaisesRegex(ValueError, "host is not allowed"):
            normalize_caddy_api_url("http://example.com:2019")

    def test_normalize_caddy_api_url_allows_local_admin_hosts(self) -> None:
        self.assertEqual(
            normalize_caddy_api_url("http://localhost:2019/"),
            "http://localhost:2019",
        )

    def test_normalize_caddy_api_url_rejects_control_characters(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid character"):
            normalize_caddy_api_url("http://localhost:2019/\x01")

    def test_normalize_caddy_api_url_rejects_excessive_length(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not exceed 2048 characters"):
            normalize_caddy_api_url("http://localhost:2019/" + ("a" * 2050))

    async def test_get_rate_limit_enabled_returns_true_by_default(self) -> None:
        async with self.session_factory() as session:
            enabled = await get_rate_limit_enabled(session)
        self.assertTrue(enabled)

    async def test_set_rate_limit_enabled_persists_value(self) -> None:
        async with self.session_factory() as session:
            await set_rate_limit_enabled(session, False)
            await session.commit()

        async with self.session_factory() as session:
            enabled = await get_rate_limit_enabled(session)
        self.assertFalse(enabled)

    async def test_set_rate_limit_enabled_to_true(self) -> None:
        async with self.session_factory() as session:
            await set_rate_limit_enabled(session, False)
            await session.commit()

        async with self.session_factory() as session:
            await set_rate_limit_enabled(session, True)
            await session.commit()

        async with self.session_factory() as session:
            enabled = await get_rate_limit_enabled(session)
        self.assertTrue(enabled)

    async def test_get_rate_limit_enabled_handles_none(self) -> None:
        async with self.session_factory() as session:
            with patch.object(runtime_settings.app_settings_repository, "get", new=AsyncMock(return_value=None)):
                enabled = await get_rate_limit_enabled(session)

        self.assertTrue(enabled)

    async def test_get_ssllabs_email_returns_none_by_default(self) -> None:
        async with self.session_factory() as session:
            email = await get_ssllabs_email(session)
        self.assertIsNone(email)

    async def test_get_ssllabs_email_handles_none(self) -> None:
        async with self.session_factory() as session:
            with patch.object(runtime_settings.app_settings_repository, "get", new=AsyncMock(return_value=None)):
                email = await get_ssllabs_email(session)

        self.assertIsNone(email)

    async def test_set_ssllabs_email_persists_normalized_value(self) -> None:
        async with self.session_factory() as session:
            await set_ssllabs_email(session, " Team@Example.COM ")
            await session.commit()

        async with self.session_factory() as session:
            email = await get_ssllabs_email(session)
        self.assertEqual(email, "team@example.com")

    async def test_set_ssllabs_email_allows_clearing_value(self) -> None:
        async with self.session_factory() as session:
            await set_ssllabs_email(session, "team@example.com")
            await session.commit()

        async with self.session_factory() as session:
            await set_ssllabs_email(session, "   ")
            await session.commit()

        async with self.session_factory() as session:
            email = await get_ssllabs_email(session)
        self.assertIsNone(email)

    async def test_get_ssllabs_retention_returns_default(self) -> None:
        async with self.session_factory() as session:
            days = await get_ssllabs_history_retention_days(session)
        self.assertEqual(days, SSLLABS_RETENTION_DEFAULT_DAYS)

    async def test_set_ssllabs_retention_persists_allowed_value(self) -> None:
        async with self.session_factory() as session:
            await set_ssllabs_history_retention_days(session, 90)
            await session.commit()

        async with self.session_factory() as session:
            days = await get_ssllabs_history_retention_days(session)
        self.assertEqual(days, 90)

    async def test_set_ssllabs_retention_rejects_disallowed_value(self) -> None:
        async with self.session_factory() as session:
            with self.assertRaisesRegex(ValueError, "must be one of"):
                await set_ssllabs_history_retention_days(session, 45)

    async def test_get_ssllabs_retention_snaps_out_of_range_value(self) -> None:
        async with self.session_factory() as session:
            with patch.object(
                runtime_settings.app_settings_repository, "get", new=AsyncMock(return_value="50")
            ):
                days = await get_ssllabs_history_retention_days(session)
        self.assertEqual(days, 30)

    async def test_get_ssllabs_retention_handles_garbage(self) -> None:
        async with self.session_factory() as session:
            with patch.object(
                runtime_settings.app_settings_repository, "get", new=AsyncMock(return_value="not-a-number")
            ):
                days = await get_ssllabs_history_retention_days(session)
        self.assertEqual(days, SSLLABS_RETENTION_DEFAULT_DAYS)

    async def test_set_caddy_config_normalizes_before_staging(self) -> None:
        async with self.session_factory() as session:
            with (
                self.assertRaisesRegex(ValueError, "must point to a file named 'Caddyfile'"),
                patch.object(runtime_settings.app_settings_repository, "set", new=AsyncMock()) as set_mock,
            ):
                await set_caddy_config(
                    session,
                    api_url="http://localhost:2019",
                    caddyfile_path="/tmp/not-caddyfile",
                )

        set_mock.assert_not_awaited()

    def test_normalize_ssllabs_email_rejects_invalid_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid email address"):
            normalize_ssllabs_email("not-an-email")

    def test_normalize_ssllabs_email_rejects_excessive_length(self) -> None:
        too_long = f"{'a' * 250}@example.com"

        with self.assertRaisesRegex(ValueError, "must not exceed 255 characters"):
            normalize_ssllabs_email(too_long)
