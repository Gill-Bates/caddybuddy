#!/usr/bin/env python3
#
# tests/test_database_session.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import threading
import tempfile
import json
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
        errors: list[BaseException] = []

        def guarded_worker() -> None:
            try:
                worker()
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=guarded_worker) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        alive_threads = [thread for thread in threads if thread.is_alive()]
        if alive_threads:
            raise AssertionError("worker thread did not finish within timeout")

        if errors:
            raise AssertionError("worker thread failed") from errors[0]

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
            database_url="sqlite+aiosqlite:///data/caddybuddy.db",
            base_dir=Path("/opt/caddybuddy"),
            data_dir=Path("/opt/caddybuddy/data"),
        )

        resolved = session_module._resolve_database_url(settings)

        self.assertEqual(resolved.database, "/opt/caddybuddy/data/caddybuddy.db")

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
            database_path = Path(temp_dir) / "caddybuddy.db"
            execution_order: list[str] = []

            with (
                patch.object(session_module, "get_settings", return_value=SimpleNamespace()),
                patch.object(session_module, "_resolve_sqlite_database_path", return_value=database_path),
                patch.object(
                    session_module,
                    "_ensure_sqlite_wal_mode",
                    side_effect=lambda: execution_order.append("wal"),
                ),
                patch.object(
                    session_module,
                    "_execute_database_init",
                    side_effect=lambda *a, **k: execution_order.append("init"),
                ),
                patch.object(
                    session_module,
                    "_acquire_sqlite_init_lock",
                    side_effect=lambda _lock_file: execution_order.append("acquire"),
                ),
                patch.object(
                    session_module,
                    "_release_sqlite_init_lock",
                    side_effect=lambda _lock_file: execution_order.append("release"),
                ),
            ):
                await session_module.init_database()

            self.assertTrue(session_module._sqlite_init_lock_path(database_path).exists())
            self.assertEqual(execution_order, ["acquire", "wal", "init", "release"])

    async def test_ensure_sqlite_wal_mode_fails_when_journal_mode_is_not_wal(self) -> None:
        class FakeResult:
            @staticmethod
            def scalar_one_or_none() -> str | None:
                return "delete"

        class FakeConnection:
            def __init__(self) -> None:
                self.statements: list[str] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def exec_driver_sql(self, statement: str):
                self.statements.append(statement)
                return FakeResult()

        class FakeEngine:
            def connect(self):
                return FakeConnection()

        with patch.object(session_module, "get_engine", return_value=FakeEngine()):
            with self.assertRaisesRegex(RuntimeError, "journal_mode='delete'"):
                await session_module._ensure_sqlite_wal_mode()

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
        fake_connection = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

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
                    "ssllabs_rank_history",
                    "caddyfile_snapshots",
                    "caddy_config_versions",
                    "caddy_sync_events",
                },
            )

        self.assertTrue(migrated)
        create_app_settings.assert_called_once_with(fake_connection, checkfirst=True)
        create_table.assert_called_once_with(fake_connection, checkfirst=True)

    def test_apply_known_table_migrations_creates_missing_ssllabs_tables(self) -> None:
        fake_connection = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        with (
            patch.object(session_module.Base.metadata.tables["app_settings"], "create") as create_app_settings,
            patch.object(session_module.Base.metadata.tables["ssllabs_targets"], "create") as create_targets,
            patch.object(session_module.Base.metadata.tables["ssllabs_scans"], "create") as create_scans,
            patch.object(session_module.Base.metadata.tables["ssllabs_rank_history"], "create") as create_history,
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
        create_history.assert_called_once_with(fake_connection, checkfirst=True)

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
                "ALTER TABLE caddy_sites ADD COLUMN site_name TEXT NOT NULL DEFAULT ''",
                "UPDATE caddy_sites SET site_name = trim(CASE WHEN instr(domain, ',') > 0 THEN substr(domain, 1, instr(domain, ',') - 1) ELSE domain END) WHERE site_name IS NULL OR site_name = ''",
                "UPDATE caddy_sites SET enabled = 0 WHERE upstream_url = 'http://placeholder.invalid'",
            ],
        )

    def test_apply_known_schema_migrations_disables_unsupported_ssllabs_schedules(self) -> None:
        executed_sql: list[str] = []

        class FakeResult:
            rowcount = 1

        class FakeConnection:
            dialect = SimpleNamespace(name="sqlite")

            def exec_driver_sql(self, statement: str):
                executed_sql.append(statement)
                return FakeResult()

        migrated = session_module._apply_known_schema_migrations(
            FakeConnection(),
            {"ssllabs_targets": {"id", "host", "schedule_frequency"}},
        )

        self.assertTrue(migrated)
        self.assertIn(
            "UPDATE ssllabs_targets SET schedule_frequency = NULL "
            "WHERE schedule_frequency IS NOT NULL AND schedule_frequency != 'weekly'",
            executed_sql,
        )

    def test_apply_known_schema_migrations_repairs_empty_upstream_url_values(self) -> None:
        executed_sql: list[str] = []

        class FakeConnection:
            dialect = SimpleNamespace(name="sqlite")

            def exec_driver_sql(self, statement: str):
                executed_sql.append(statement)
                if "UPDATE caddy_sites SET upstream_url" in statement:
                    return SimpleNamespace(rowcount=1)
                if "UPDATE caddy_sites SET site_name" in statement:
                    return SimpleNamespace(rowcount=1)
                return None

        migrated = session_module._apply_known_schema_migrations(
            FakeConnection(),
            {"caddy_sites": {"id", "domain", "upstream_url", "caddy_directives", "enabled"}},
        )

        self.assertTrue(migrated)
        self.assertEqual(
            executed_sql,
            [
                "UPDATE caddy_sites SET upstream_url = 'http://placeholder.invalid' WHERE upstream_url = ''",
                "ALTER TABLE caddy_sites ADD COLUMN site_name TEXT NOT NULL DEFAULT ''",
                "UPDATE caddy_sites SET site_name = trim(CASE WHEN instr(domain, ',') > 0 THEN substr(domain, 1, instr(domain, ',') - 1) ELSE domain END) WHERE site_name IS NULL OR site_name = ''",
            ],
        )

    def test_apply_known_schema_migrations_uses_tuple_parameters_for_onboarding_state_insert(self) -> None:
        captured: list[tuple[str, tuple[object, ...]]] = []

        class FakeInsertResult:
            rowcount = 1

        class FakeDdlResult:
            rowcount = 0

            @staticmethod
            def first():
                return (
                    "CREATE TABLE app_settings (id INTEGER NOT NULL, key VARCHAR(64) NOT NULL, "
                    "value TEXT NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
                    "CONSTRAINT ck_app_settings_key CHECK (key IN "
                    "('caddy_api_url', 'caddyfile_path', 'rate_limit_enabled', "
                    "'ssllabs_email', 'ssllabs_history_retention_days')))",
                )

        class FakeConnection:
            dialect = SimpleNamespace(name="sqlite")

            def exec_driver_sql(self, statement: str, params: tuple[object, ...] | None = None):
                captured.append((statement, params))
                if statement == "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'app_settings'":
                    return FakeDdlResult()
                return FakeInsertResult()

        migrated = session_module._apply_known_schema_migrations(
            FakeConnection(),
            {
                "caddybuddy_state": {"key", "value", "updated_at"},
                "app_settings": {"id", "key", "value", "created_at", "updated_at"},
                "caddy_sites": {"id", "domain", "upstream_url", "caddy_directives", "enabled", "site_name"},
                "caddy_sync_events": {"id"},
            },
        )

        self.assertTrue(migrated)
        param_calls = [(statement, params) for statement, params in captured if params is not None]
        self.assertEqual(len(param_calls), 1)
        statement, params = param_calls[0]
        self.assertIn("INSERT INTO caddybuddy_state (key, value, updated_at)", statement)
        self.assertIn("EXISTS (SELECT 1 FROM app_settings)", statement)
        self.assertIn("EXISTS (SELECT 1 FROM caddy_sites)", statement)
        self.assertIsInstance(params, tuple)
        self.assertEqual(len(params), 2)
        payload = json.loads(params[0])
        self.assertEqual(payload["status"], "completed")
        self.assertRegex(payload["completed_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertRegex(params[1], r"^\d{4}-\d{2}-\d{2}T")

    def test_apply_known_schema_migrations_skips_onboarding_state_for_empty_factory_default(self) -> None:
        class FakeConnection:
            dialect = SimpleNamespace(name="sqlite")

            def exec_driver_sql(self, statement: str, params: tuple[object, ...] | None = None):
                raise AssertionError(f"Unexpected SQL executed: {statement}")

        migrated = session_module._apply_known_schema_migrations(
            FakeConnection(),
            {"caddybuddy_state": {"key", "value", "updated_at"}},
        )

        self.assertFalse(migrated)

    def test_apply_known_schema_migrations_does_not_report_repairs_when_no_rows_change(self) -> None:
        class FakeConnection:
            dialect = SimpleNamespace(name="sqlite")

            def exec_driver_sql(self, statement: str):
                if statement.startswith("UPDATE caddy_sites"):
                    return SimpleNamespace(rowcount=0)
                raise AssertionError(f"Unexpected SQL executed: {statement}")

        migrated = session_module._apply_known_schema_migrations(
            FakeConnection(),
            {
                "caddy_sites": {
                    "id",
                    "domain",
                    "upstream_url",
                    "caddy_directives",
                    "enabled",
                    "site_name",
                },
            },
        )

        self.assertFalse(migrated)

    def test_apply_known_schema_migrations_rebuilds_app_settings_for_new_allowed_key(self) -> None:
        executed_sql: list[str] = []

        class FakeResult:
            @staticmethod
            def first() -> tuple[str] | None:
                return (
                    "CREATE TABLE app_settings (id INTEGER NOT NULL, \"key\" VARCHAR(64) NOT NULL, "
                    "value TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                    "CONSTRAINT ck_app_settings_key CHECK (key IN ('caddy_api_url', 'caddyfile_path', 'rate_limit_enabled')))"
                ,)

        class FakeConnection:
            dialect = SimpleNamespace(name="sqlite")

            def exec_driver_sql(self, statement: str):
                if statement == "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'app_settings'":
                    return FakeResult()
                executed_sql.append(statement)
                return None

        connection = FakeConnection()

        with (
            patch.object(session_module.Base.metadata.tables["app_settings"], "create") as create_app_settings,
            patch.object(
                session_module,
                "_sqlite_backup_table_name",
                return_value="app_settings_backup_test",
            ),
        ):
            migrated = session_module._apply_known_schema_migrations(
                connection,
                {"app_settings": {"id", "key", "value", "created_at", "updated_at"}},
            )

        self.assertTrue(migrated)
        self.assertEqual(
            executed_sql,
            [
                'DROP TABLE IF EXISTS "app_settings_backup_test"',
                'CREATE TABLE "app_settings_backup_test" AS SELECT id, "key", value, created_at, updated_at FROM app_settings',
                "DROP TABLE app_settings",
                'INSERT INTO app_settings (id, "key", value, created_at, updated_at) SELECT id, "key", value, created_at, updated_at FROM "app_settings_backup_test"',
                'DROP TABLE "app_settings_backup_test"',
            ],
        )
        create_app_settings.assert_called_once_with(connection, checkfirst=True)

    def test_app_settings_allows_current_keys_detects_all_keys(self) -> None:
        table_sql = """
            CREATE TABLE app_settings (
                id INTEGER NOT NULL,
                "key" VARCHAR(64) NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CONSTRAINT ck_app_settings_key CHECK (key IN ('caddy_api_url', 'caddyfile_path', 'rate_limit_enabled', 'ssllabs_email', 'ssllabs_history_retention_days'))
            )
        """

        self.assertTrue(session_module._app_settings_allows_current_keys(table_sql))

    def test_app_settings_allows_current_keys_requires_retention_key(self) -> None:
        # DDL predating the retention key must trigger an app_settings rebuild.
        table_sql = """
            CREATE TABLE app_settings (
                id INTEGER NOT NULL,
                "key" VARCHAR(64) NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CONSTRAINT ck_app_settings_key CHECK (key IN ('caddy_api_url', 'caddyfile_path', 'rate_limit_enabled', 'ssllabs_email'))
            )
        """

        self.assertFalse(session_module._app_settings_allows_current_keys(table_sql))

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
