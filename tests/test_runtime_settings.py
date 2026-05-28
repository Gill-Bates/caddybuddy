#!/usr/bin/env python3
#
# tests/test_runtime_settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.services.runtime_settings import (
    get_caddy_config,
    get_rate_limit_enabled,
    get_ssllabs_email,
    normalize_caddy_api_url,
    normalize_caddyfile_path,
    normalize_ssllabs_email,
    set_caddy_api_url,
    set_caddyfile_path,
    set_rate_limit_enabled,
    set_ssllabs_email,
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

    async def test_get_ssllabs_email_returns_none_by_default(self) -> None:
        async with self.session_factory() as session:
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

    def test_normalize_ssllabs_email_rejects_invalid_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid email address"):
            normalize_ssllabs_email("not-an-email")