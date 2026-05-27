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
from app.services.runtime_settings import get_caddy_config, set_caddy_api_url, set_caddyfile_path


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