#!/usr/bin/env python3

from __future__ import annotations

import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app.database.session as session_module


class _SessionModuleStateMixin:
    def tearDown(self) -> None:
        session_module._engine = None
        session_module._session_factory = None


class DatabaseSessionLazyInitTests(_SessionModuleStateMixin, unittest.TestCase):
    def _run_workers(self, count: int, worker) -> None:
        threads = [threading.Thread(target=worker) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def test_get_engine_creates_single_instance_under_contention(self) -> None:
        barrier = threading.Barrier(4)
        created_engines: list[object] = []
        returned_engines: list[object] = []

        def fake_create_engine() -> object:
            engine = object()
            created_engines.append(engine)
            return engine

        def worker() -> None:
            barrier.wait()
            returned_engines.append(session_module.get_engine())

        with patch.object(session_module, "_create_engine", side_effect=fake_create_engine):
            self._run_workers(4, worker)

        self.assertEqual(len(created_engines), 1)
        self.assertEqual(len({id(engine) for engine in returned_engines}), 1)

    def test_get_session_factory_creates_single_instance_under_contention(self) -> None:
        barrier = threading.Barrier(4)
        sentinel_engine = object()
        created_factories: list[object] = []
        returned_factories: list[object] = []

        def fake_async_sessionmaker(**kwargs) -> object:
            self.assertIs(kwargs["bind"], sentinel_engine)
            factory = object()
            created_factories.append(factory)
            return factory

        def worker() -> None:
            barrier.wait()
            returned_factories.append(session_module.get_session_factory())

        with (
            patch.object(session_module, "get_engine", return_value=sentinel_engine),
            patch.object(session_module, "async_sessionmaker", side_effect=fake_async_sessionmaker),
        ):
            self._run_workers(4, worker)

        self.assertEqual(len(created_factories), 1)
        self.assertEqual(len({id(factory) for factory in returned_factories}), 1)

    def test_create_engine_omits_sqlite_connect_args_for_non_sqlite_urls(self) -> None:
        settings = SimpleNamespace(
            database_url="postgresql+asyncpg://user:password@localhost/dbname",
            base_dir=Path("/opt/caddybuddy"),
        )
        fake_engine = SimpleNamespace(url=session_module.make_url(settings.database_url), sync_engine=object())

        with (
            patch.object(session_module, "get_settings", return_value=settings),
            patch.object(session_module, "create_async_engine", return_value=fake_engine) as create_async_engine,
            patch.object(session_module.event, "listen") as listen,
        ):
            engine = session_module._create_engine()

        self.assertIs(engine, fake_engine)
        self.assertEqual(create_async_engine.call_args.kwargs["poolclass"], session_module.NullPool)
        self.assertNotIn("connect_args", create_async_engine.call_args.kwargs)
        listen.assert_not_called()


class DatabaseSessionInitTests(_SessionModuleStateMixin, unittest.IsolatedAsyncioTestCase):
    async def test_init_database_uses_process_lock_for_file_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "app.db"
            execute_database_init = AsyncMock()

            with (
                patch.object(session_module, "get_settings", return_value=SimpleNamespace()),
                patch.object(session_module, "_resolve_sqlite_database_path", return_value=database_path),
                patch.object(session_module, "_execute_database_init", execute_database_init),
                patch.object(session_module.fcntl, "flock") as flock,
            ):
                await session_module.init_database()

            self.assertTrue(session_module._sqlite_init_lock_path(database_path).exists())
            execute_database_init.assert_awaited_once()
            self.assertEqual(flock.call_count, 2)


if __name__ == "__main__":
    unittest.main()