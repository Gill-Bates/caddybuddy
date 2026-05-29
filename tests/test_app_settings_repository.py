#!/usr/bin/env python3
#
# tests/test_app_settings_repository.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.repositories.app_settings import AppSettingsRepository


class AppSettingsRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_rejects_unknown_key(self) -> None:
        repository = AppSettingsRepository()
        session = SimpleNamespace()

        with self.assertRaisesRegex(ValueError, "Unknown app setting key"):
            await repository.get(session, "unknown_key")

    async def test_get_all_filters_unknown_rows(self) -> None:
        repository = AppSettingsRepository()
        session = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    all=lambda: [
                        SimpleNamespace(key="caddy_api_url", value="http://localhost:2019"),
                        SimpleNamespace(key="unexpected", value="ignored"),
                    ]
                )
            )
        )

        settings = await repository.get_all(session)

        self.assertEqual(settings["caddy_api_url"], "http://localhost:2019")
        self.assertNotIn("unexpected", settings)


class AppSettingsRepositoryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self._temp_dir.name) / "settings.db"
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

    async def test_set_upserts_existing_setting(self) -> None:
        repository = AppSettingsRepository()

        async with self.session_factory() as session:
            first = await repository.set(session, "caddy_api_url", "http://localhost:2019")
            await session.commit()

        async with self.session_factory() as session:
            second = await repository.set(session, "caddy_api_url", "http://127.0.0.1:2019")
            await session.commit()

            stored = await repository.get(session, "caddy_api_url")

        self.assertEqual(first.id, second.id)
        self.assertEqual(stored, "http://127.0.0.1:2019")


if __name__ == "__main__":
    unittest.main()