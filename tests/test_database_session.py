#!/usr/bin/env python3
#
# tests/test_database_session.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

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
            data_dir=Path("/opt/caddybuddy/data"),
        )
        fake_engine = SimpleNamespace(url=session_module.make_url(settings.database_url), sync_engine=object())

        with (
            patch.object(session_module, "get_settings", return_value=settings),
            patch.object(session_module, "create_async_engine", return_value=fake_engine) as create_async_engine,
            patch.object(session_module.event, "listen") as listen,
        ):
            engine = session_module._create_engine()

        self.assertIs(engine, fake_engine)
        self.assertTrue(create_async_engine.call_args.kwargs["pool_pre_ping"])
        self.assertNotIn("poolclass", create_async_engine.call_args.kwargs)
        self.assertNotIn("connect_args", create_async_engine.call_args.kwargs)
        listen.assert_not_called()

    def test_create_engine_uses_static_pool_for_sqlite_memory(self) -> None:
        settings = SimpleNamespace(
            database_url="sqlite+aiosqlite:///:memory:",
            base_dir=Path("/opt/caddybuddy"),
            data_dir=Path("/opt/caddybuddy/data"),
        )
        fake_engine = SimpleNamespace(url=session_module.make_url(settings.database_url), sync_engine=object())

        with (
            patch.object(session_module, "get_settings", return_value=settings),
            patch.object(session_module, "create_async_engine", return_value=fake_engine) as create_async_engine,
            patch.object(session_module.event, "listen") as listen,
        ):
            engine = session_module._create_engine()

        self.assertIs(engine, fake_engine)
        self.assertEqual(create_async_engine.call_args.kwargs["poolclass"], session_module.StaticPool)
        self.assertEqual(create_async_engine.call_args.kwargs["connect_args"], {"check_same_thread": False})
        listen.assert_called_once()

    def test_resolve_database_url_keeps_sqlite_database_inside_data_directory(self) -> None:
        settings = SimpleNamespace(
            database_url="sqlite+aiosqlite:///data/app.db",
            base_dir=Path("/opt/caddybuddy"),
            data_dir=Path("/opt/caddybuddy/data"),
        )

        resolved = session_module._resolve_database_url(settings)

        self.assertEqual(resolved.database, "/opt/caddybuddy/data/app.db")

    def test_resolve_database_url_rejects_sqlite_database_outside_data_directory(self) -> None:
        settings = SimpleNamespace(
            database_url="sqlite+aiosqlite:////tmp/app.db",
            base_dir=Path("/opt/caddybuddy"),
            data_dir=Path("/opt/caddybuddy/data"),
        )

        with self.assertRaisesRegex(RuntimeError, "SQLite database path must be inside data directory"):
            session_module._resolve_database_url(settings)


class DatabaseSessionInitTests(_SessionModuleStateMixin, unittest.IsolatedAsyncioTestCase):
    async def test_init_database_keeps_memory_sqlite_schema_available_across_sessions(self) -> None:
        settings = SimpleNamespace(
            database_url="sqlite+aiosqlite:///:memory:",
            base_dir=Path("/tmp"),
            data_dir=Path("/tmp"),
        )

        with patch.object(session_module, "get_settings", return_value=settings):
            await session_module.init_database()
            async with session_module.get_session_factory()() as session:
                result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
                tables = {row[0] for row in result}

        self.assertIn("users", tables)
        self.assertIn("caddy_sites", tables)
        await session_module.dispose_engine()

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

    async def test_dispose_engine_closes_engine_and_resets_lazy_state(self) -> None:
        fake_engine = AsyncMock()
        fake_factory = object()
        session_module._engine = fake_engine
        session_module._session_factory = fake_factory

        await session_module.dispose_engine()

        fake_engine.dispose.assert_awaited_once()
        self.assertIsNone(session_module._engine)
        self.assertIsNone(session_module._session_factory)

    async def test_dispose_engine_is_noop_without_engine(self) -> None:
        session_module._engine = None
        session_module._session_factory = object()

        await session_module.dispose_engine()

        self.assertIsNone(session_module._engine)
        self.assertIsNone(session_module._session_factory)


class DatabaseSessionMigrationTests(_SessionModuleStateMixin, unittest.TestCase):
    def test_apply_known_table_migrations_creates_missing_caddy_state_table(self) -> None:
        fake_connection = object()

        with (
            patch.object(session_module.Base.metadata.tables["app_settings"], "create") as create_app_settings,
            patch.object(
                session_module.Base.metadata.tables["caddybuddy_state"],
                "create",
            ) as create_table,
        ):
            migrated = session_module._apply_known_table_migrations(
                fake_connection,
                {
                    "users",
                    "caddy_sites",
                    "ssllabs_targets",
                    "ssllabs_scans",
                    "caddyfile_snapshots",
                    "caddy_config_versions",
                    "caddy_sync_events",
                },
            )

        self.assertTrue(migrated)
        create_app_settings.assert_called_once_with(fake_connection, checkfirst=True)
        create_table.assert_called_once_with(fake_connection, checkfirst=True)

    def test_apply_known_table_migrations_creates_missing_ssllabs_tables(self) -> None:
        fake_connection = object()

        with (
            patch.object(session_module.Base.metadata.tables["app_settings"], "create") as create_app_settings,
            patch.object(session_module.Base.metadata.tables["ssllabs_targets"], "create") as create_targets,
            patch.object(session_module.Base.metadata.tables["ssllabs_scans"], "create") as create_scans,
        ):
            migrated = session_module._apply_known_table_migrations(
                fake_connection,
                {
                    "users",
                    "caddy_sites",
                    "caddybuddy_state",
                    "caddyfile_snapshots",
                    "caddy_config_versions",
                    "caddy_sync_events",
                },
            )

        self.assertTrue(migrated)
        create_app_settings.assert_called_once_with(fake_connection, checkfirst=True)
        create_targets.assert_called_once_with(fake_connection, checkfirst=True)
        create_scans.assert_called_once_with(fake_connection, checkfirst=True)

    def test_apply_known_schema_migrations_adds_caddy_sites_columns(self) -> None:
        executed_sql: list[str] = []

        class FakeConnection:
            dialect = SimpleNamespace(name="sqlite")

            def exec_driver_sql(self, statement: str) -> None:
                executed_sql.append(statement)

        migrated = session_module._apply_known_schema_migrations(
            FakeConnection(),
            {
                "caddy_sites": {"id", "domain"},
            },
        )

        self.assertTrue(migrated)
        self.assertEqual(
            executed_sql,
            [
                "ALTER TABLE caddy_sites ADD COLUMN upstream_url TEXT NOT NULL DEFAULT 'http://placeholder.invalid'",
                "ALTER TABLE caddy_sites ADD COLUMN caddy_directives TEXT",
                "ALTER TABLE caddy_sites ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1",
            ],
        )

    def test_apply_known_schema_migrations_repairs_empty_upstream_url_values(self) -> None:
        executed_sql: list[str] = []

        class FakeConnection:
            dialect = SimpleNamespace(name="sqlite")

            def exec_driver_sql(self, statement: str) -> None:
                executed_sql.append(statement)

        migrated = session_module._apply_known_schema_migrations(
            FakeConnection(),
            {"caddy_sites": {"id", "domain", "upstream_url", "caddy_directives", "enabled"}},
        )

        self.assertTrue(migrated)
        self.assertEqual(
            executed_sql,
            [
                "UPDATE caddy_sites SET upstream_url = 'http://placeholder.invalid' WHERE upstream_url = ''",
            ],
        )

    def test_apply_known_schema_migrations_is_noop_for_non_sqlite(self) -> None:
        class FakeConnection:
            dialect = SimpleNamespace(name="postgresql")

            def exec_driver_sql(self, statement: str) -> None:
                raise AssertionError(f"Unexpected SQL executed: {statement}")

        migrated = session_module._apply_known_schema_migrations(
            FakeConnection(),
            {"caddy_sites": {"id", "domain"}},
        )

        self.assertFalse(migrated)

    def test_read_existing_unique_constraints_includes_unique_indexes(self) -> None:
        requested_tables: list[str] = []

        class FakeInspector:
            def get_table_names(self):
                return ["caddy_sites"]

            def get_unique_constraints(self, table_name):
                requested_tables.append(table_name)
                return []

            def get_indexes(self, table_name):
                requested_tables.append(table_name)
                return [{"unique": True, "column_names": ["domain"]}]

        with patch.object(session_module, "inspect", return_value=FakeInspector()):
            constraints = session_module._read_existing_unique_constraints(object())

        self.assertEqual(constraints, {"caddy_sites": {("domain",)}})
        self.assertEqual(requested_tables, ["caddy_sites", "caddy_sites"])


if __name__ == "__main__":
    unittest.main()