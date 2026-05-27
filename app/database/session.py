#!/usr/bin/env python3
#
# app/database/session.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from app.config.settings import Settings, get_settings
from app.models import entities as _entities  # noqa: F401
from app.models.base import Base


logger = logging.getLogger(__name__)
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_lazy_init_lock = threading.RLock()


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    """Apply SQLite connection pragmas required by the application."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def _create_engine() -> AsyncEngine:
    """Create the shared async engine from current settings."""
    settings = get_settings()
    database_url = _resolve_database_url(settings)
    engine_kwargs: dict[str, object] = {}
    if database_url.get_backend_name() == "sqlite":
        if _is_sqlite_memory_url(database_url):
            engine_kwargs["poolclass"] = StaticPool
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        else:
            engine_kwargs["poolclass"] = NullPool
            engine_kwargs["connect_args"] = {"timeout": 30}
    else:
        engine_kwargs["pool_pre_ping"] = True

    engine = create_async_engine(database_url, **engine_kwargs)
    if engine.url.get_backend_name() == "sqlite":
        event.listen(engine.sync_engine, "connect", _configure_sqlite)
    return engine


def _is_sqlite_memory_url(database_url: URL) -> bool:
    return database_url.get_backend_name() == "sqlite" and database_url.database in {None, "", ":memory:"}


def _resolve_database_url(settings: Settings) -> URL:
    """Return the effective database URL with SQLite files constrained to data/."""
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite":
        return url

    database = url.database
    if database in {None, "", ":memory:"}:
        return url

    database_path = Path(database)
    if not database_path.is_absolute():
        database_path = settings.base_dir / database_path

    resolved_database_path = database_path.resolve()
    allowed_root = settings.data_dir.resolve()
    if resolved_database_path != allowed_root and allowed_root not in resolved_database_path.parents:
        raise RuntimeError(f"SQLite database path must be inside data directory: {allowed_root}")

    return url.set(database=str(resolved_database_path))


def get_engine() -> AsyncEngine:
    """Return the lazily initialized async engine.

    Initialization is protected by a re-entrant process-local lock so parallel
    callers do not create duplicate engines.
    """
    global _engine
    if _engine is None:
        with _lazy_init_lock:
            if _engine is None:
                _engine = _create_engine()
    assert _engine is not None
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the lazily initialized async session factory.

    Initialization is protected by a re-entrant process-local lock so parallel
    callers do not create duplicate session factories.
    """
    global _session_factory
    if _session_factory is None:
        with _lazy_init_lock:
            if _session_factory is None:
                _session_factory = async_sessionmaker(
                    bind=get_engine(),
                    expire_on_commit=False,
                    class_=AsyncSession,
                )
    assert _session_factory is not None
    return _session_factory


def _resolve_sqlite_database_path(settings: Settings) -> Path | None:
    """Resolve the configured SQLite database file path if the URL is file-based."""
    database_url = _resolve_database_url(settings)
    if database_url.get_backend_name() != "sqlite":
        return None

    database = database_url.database
    if database in {None, "", ":memory:"}:
        return None

    return Path(database)


def _read_existing_table_names(sync_connection) -> set[str]:
    """Return the table names currently present in the connected schema."""
    return set(inspect(sync_connection).get_table_names())


def _read_existing_table_columns(sync_connection) -> dict[str, set[str]]:
    """Return the current column names for every table in the connected schema."""
    schema_inspector = inspect(sync_connection)
    return {
        table_name: {column["name"] for column in schema_inspector.get_columns(table_name)}
        for table_name in schema_inspector.get_table_names()
    }


def _read_existing_unique_constraints(sync_connection) -> dict[str, set[tuple[str, ...]]]:
    """Return table-level unique constraints as normalized column tuples."""
    schema_inspector = inspect(sync_connection)
    table_constraints: dict[str, set[tuple[str, ...]]] = {}
    for table_name in schema_inspector.get_table_names():
        constraints: set[tuple[str, ...]] = set()
        for constraint in schema_inspector.get_unique_constraints(table_name):
            column_names = constraint.get("column_names") or []
            if column_names:
                constraints.add(tuple(column_names))
        for index in schema_inspector.get_indexes(table_name):
            if not index.get("unique"):
                continue
            column_names = index.get("column_names") or []
            if column_names:
                constraints.add(tuple(column_names))
        table_constraints[table_name] = constraints
    return table_constraints


def _apply_known_table_migrations(sync_connection, existing_tables: set[str]) -> bool:
    """Create newly required tables for existing databases."""
    migrated = False
    for table_name in (
        "app_settings",
        "caddybuddy_state",
        "caddyfile_snapshots",
        "caddy_config_versions",
        "caddy_sync_events",
        "ssllabs_targets",
        "ssllabs_scans",
    ):
        if table_name in existing_tables:
            continue
        table = Base.metadata.tables.get(table_name)
        if table is None:
            raise RuntimeError(f"Known migration table not present in metadata: {table_name}")
        logger.info("Creating missing table during database init: %s", table_name)
        table.create(sync_connection, checkfirst=True)
        migrated = True
    return migrated


def _apply_known_schema_migrations(
    sync_connection,
    existing_columns: dict[str, set[str]],
) -> bool:
    """Apply narrowly scoped schema migrations required by the app."""
    dialect_name = getattr(getattr(sync_connection, "dialect", None), "name", None)
    if dialect_name not in {None, "sqlite"}:
        return False

    migrated = False

    site_columns = existing_columns.get("caddy_sites")
    if site_columns is not None and "upstream_url" not in site_columns:
        logger.info("Applying known SQLite schema migration: caddy_sites.upstream_url")
        sync_connection.exec_driver_sql(
            "ALTER TABLE caddy_sites ADD COLUMN upstream_url TEXT NOT NULL DEFAULT ''"
        )
        migrated = True
    if site_columns is not None and "caddy_directives" not in site_columns:
        logger.info("Applying known SQLite schema migration: caddy_sites.caddy_directives")
        sync_connection.exec_driver_sql(
            "ALTER TABLE caddy_sites ADD COLUMN caddy_directives TEXT"
        )
        migrated = True
    if site_columns is not None and "enabled" not in site_columns:
        logger.info("Applying known SQLite schema migration: caddy_sites.enabled")
        sync_connection.exec_driver_sql(
            "ALTER TABLE caddy_sites ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
        )
        migrated = True

    return migrated


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield a database session.

    The caller owns transaction boundaries and must explicitly commit or roll
    back. Uncommitted changes are discarded automatically when the session is
    closed at dependency teardown.
    """
    async with get_session_factory()() as session:
        yield session


def _current_process_uid() -> int | str:
    """Return the current process uid when available."""
    get_uid = getattr(os, "getuid", None)
    if callable(get_uid):
        return get_uid()
    return "unknown"


def _ensure_sqlite_database_directory(database_path: Path) -> None:
    """Ensure the configured SQLite directory exists and is writable."""
    data_dir = database_path.parent
    data_dir.mkdir(parents=True, exist_ok=True)

    if not data_dir.is_dir():
        raise RuntimeError(
            f"Database directory does not exist and could not be created: {data_dir}"
        )
    if not os.access(data_dir, os.W_OK):
        raise RuntimeError(
            f"Database directory is not writable: {data_dir}. "
            f"Ensure the directory exists with correct permissions (uid={_current_process_uid()})."
        )
    if database_path.exists() and not os.access(database_path, os.W_OK):
        raise RuntimeError(f"SQLite database file is not writable: {database_path}")


def _sqlite_init_lock_path(database_path: Path) -> Path:
    """Return the sidecar lock file used to serialize SQLite initialization."""
    return database_path.with_name(f".{database_path.name}.init.lock")


def _acquire_sqlite_init_lock(lock_file) -> None:
    if fcntl is None:
        raise RuntimeError("SQLite initialization lock requires fcntl on this platform.")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _release_sqlite_init_lock(lock_file) -> None:
    if fcntl is None:
        raise RuntimeError("SQLite initialization lock requires fcntl on this platform.")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


async def _execute_database_init() -> None:
    """Perform schema bootstrap or schema validation for the current database."""
    async with get_engine().begin() as connection:
        existing_tables = await connection.run_sync(_read_existing_table_names)
        if not existing_tables:
            logger.info("Initializing new database schema")
            await connection.run_sync(Base.metadata.create_all)
            return

        migrated_tables = await connection.run_sync(_apply_known_table_migrations, existing_tables)
        if migrated_tables:
            existing_tables = await connection.run_sync(_read_existing_table_names)

        expected_tables = set(Base.metadata.tables)
        missing_tables = sorted(expected_tables.difference(existing_tables))
        if missing_tables:
            logger.error("Database schema missing tables: %s", ", ".join(missing_tables))
            raise RuntimeError(
                "Existing database schema is missing tables: "
                f"{', '.join(missing_tables)}. "
                "Startup will not mutate an existing schema automatically. "
                "Reinitialize the database or add migrations before starting the app."
            )

        existing_columns = await connection.run_sync(_read_existing_table_columns)
        migrated = await connection.run_sync(_apply_known_schema_migrations, existing_columns)
        if migrated:
            existing_columns = await connection.run_sync(_read_existing_table_columns)

        missing_columns: list[str] = []
        for table_name, table in Base.metadata.tables.items():
            current_columns = existing_columns.get(table_name, set())
            absent_columns = sorted(set(table.columns.keys()).difference(current_columns))
            missing_columns.extend(f"{table_name}.{column_name}" for column_name in absent_columns)

        if missing_columns:
            logger.error("Database schema missing columns: %s", ", ".join(missing_columns))
            raise RuntimeError(
                "Existing database schema is missing columns: "
                f"{', '.join(missing_columns)}. "
                "Startup will not mutate an existing schema automatically. "
                "Reinitialize the database or add migrations before starting the app."
            )

        existing_unique_constraints = await connection.run_sync(_read_existing_unique_constraints)
        missing_unique_constraints: list[str] = []
        for table_name, table in Base.metadata.tables.items():
            expected_constraints = {
                tuple(column.name for column in constraint.columns)
                for constraint in table.constraints
                if getattr(constraint, "__visit_name__", "") == "unique_constraint"
            }
            current_constraints = existing_unique_constraints.get(table_name, set())
            for expected_constraint in sorted(expected_constraints):
                if expected_constraint not in current_constraints:
                    missing_unique_constraints.append(
                        f"{table_name}({', '.join(expected_constraint)})"
                    )

        if missing_unique_constraints:
            logger.error(
                "Database schema missing unique constraints: %s",
                ", ".join(missing_unique_constraints),
            )
            raise RuntimeError(
                "Existing database schema is missing unique constraints: "
                f"{', '.join(missing_unique_constraints)}. "
                "Startup will not mutate an existing schema automatically. "
                "Reinitialize the database or add migrations before starting the app."
            )


async def init_database() -> None:
    """Initialize schema and refuse implicit upgrades of existing databases.

    File-based SQLite databases use a Linux advisory file lock to serialize
    startup initialization across concurrent worker processes.
    """
    settings = get_settings()
    database_path = _resolve_sqlite_database_path(settings)

    if database_path is None:
        await _execute_database_init()
        return

    _ensure_sqlite_database_directory(database_path)
    lock_path = _sqlite_init_lock_path(database_path)
    logger.debug("Acquiring SQLite init lock: %s", lock_path)
    with lock_path.open("a+b") as lock_file:
        await asyncio.to_thread(_acquire_sqlite_init_lock, lock_file)
        try:
            await _execute_database_init()
        finally:
            await asyncio.to_thread(_release_sqlite_init_lock, lock_file)
            logger.debug("Released SQLite init lock: %s", lock_path)


async def dispose_engine() -> None:
    """Dispose the shared engine and reset lazy session state."""
    global _engine, _session_factory
    with _lazy_init_lock:
        engine = _engine
        _engine = None
        _session_factory = None
    if engine is None:
        return
    await engine.dispose()