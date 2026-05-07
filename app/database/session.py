#!/usr/bin/env python3
#
# app/database/session.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import os
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import Settings, get_settings
from app.models import entities as _entities  # noqa: F401
from app.models.base import Base


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    """Apply SQLite connection pragmas required by the application."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def _create_engine() -> AsyncEngine:
    """Create the shared async engine from current settings."""
    settings = get_settings()
    database_url = _resolve_database_url(settings)
    engine = create_async_engine(
        database_url,
        connect_args={"timeout": 30},
        poolclass=NullPool,
    )
    if engine.url.get_backend_name() == "sqlite":
        event.listen(engine.sync_engine, "connect", _configure_sqlite)
    return engine


def _resolve_database_url(settings: Settings) -> URL:
    """Return the effective database URL with project-relative SQLite paths normalized."""
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite":
        return url

    database = url.database
    if database in {None, "", ":memory:"}:
        return url

    database_path = Path(database)
    if not database_path.is_absolute():
        database_path = settings.base_dir / database_path

    return url.set(database=str(database_path))


def get_engine() -> AsyncEngine:
    """Return the lazily initialized async engine."""
    global _engine
    if _engine is None:
        _engine = _create_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the lazily initialized async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
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


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield a database session without changing transaction semantics."""
    async with get_session_factory()() as session:
        yield session


async def init_database() -> None:
    """Initialize schema and refuse implicit upgrades of existing databases."""
    settings = get_settings()
    database_path = _resolve_sqlite_database_path(settings)

    if database_path is not None:
        data_dir = database_path.parent
        data_dir.mkdir(parents=True, exist_ok=True)

        if not data_dir.is_dir():
            raise RuntimeError(
                f"Database directory does not exist and could not be created: {data_dir}"
            )
        if not os.access(data_dir, os.W_OK):
            raise RuntimeError(
                f"Database directory is not writable: {data_dir}. "
                f"Ensure the directory exists with correct permissions (uid={os.getuid()})."
            )

    async with get_engine().begin() as connection:
        existing_tables = await connection.run_sync(_read_existing_table_names)
        if not existing_tables:
            await connection.run_sync(Base.metadata.create_all)
            return

        expected_tables = set(Base.metadata.tables)
        missing_tables = sorted(expected_tables.difference(existing_tables))
        if missing_tables:
            raise RuntimeError(
                "Existing database schema is missing tables: "
                f"{', '.join(missing_tables)}. "
                "Startup will not mutate an existing schema automatically. "
                "Reinitialize the database or add migrations before starting the app."
            )


async def dispose_engine() -> None:
    """Dispose the shared engine and reset lazy session state."""
    global _engine, _session_factory
    if _engine is None:
        return
    await _engine.dispose()
    _engine = None
    _session_factory = None