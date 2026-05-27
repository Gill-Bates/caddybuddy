#!/usr/bin/env python3
#
# tests/test_user_repository.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app.services.auth as auth_module
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.repositories.users import UserRepository


class UserRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_by_username_normalizes_input_before_query(self) -> None:
        session = SimpleNamespace()
        repository = UserRepository()

        async def execute(statement):
            self.assertEqual(str(statement.compile(compile_kwargs={"literal_binds": True})), (
                "SELECT users.id, users.username, users.email, users.password_hash, users.role, users.is_active, users.last_login, users.created_at, users.updated_at \n"
                "FROM users \n"
                "WHERE lower(users.username) = 'admin'"
            ))
            return SimpleNamespace(scalar_one_or_none=lambda: None)

        session.execute = execute

        result = await repository.get_by_username(session, "  Admin  ")

        self.assertIsNone(result)

    async def test_create_normalizes_username_and_email(self) -> None:
        session = SimpleNamespace(add=lambda user: None, flush=AsyncMock())
        repository = UserRepository()

        user = await repository.create(
            session,
            username="  Admin  ",
            email="  USER@Example.COM  ",
            password_hash="hash",
            role="admin",
        )

        self.assertEqual(user.username, "Admin")
        self.assertEqual(user.email, "user@example.com")
        session.flush.assert_awaited_once()

    async def test_create_rejects_blank_username(self) -> None:
        session = SimpleNamespace(add=lambda user: None, flush=AsyncMock())
        repository = UserRepository()

        with self.assertRaisesRegex(ValueError, "Username must not be empty"):
            await repository.create(
                session,
                username="   ",
                email=None,
                password_hash="hash",
            )

        session.flush.assert_not_awaited()

    async def test_update_last_login_requires_timezone_aware_datetime(self) -> None:
        session = SimpleNamespace(flush=AsyncMock())
        repository = UserRepository()
        user = SimpleNamespace(last_login=None)

        with self.assertRaisesRegex(ValueError, "last_login must be timezone-aware"):
            await repository.update_last_login(session, user, datetime.now())

        session.flush.assert_not_awaited()

    async def test_exists_any_returns_true_when_user_row_exists(self) -> None:
        session = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 1))
        )
        repository = UserRepository()

        self.assertTrue(await repository.exists_any(session))


class AuthBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_default_admin_uses_exists_any_short_circuit(self) -> None:
        session = object()

        with (
            patch.object(auth_module.user_repository, "exists_any", new=AsyncMock(return_value=True)) as exists_any,
            patch.object(auth_module.user_repository, "create", new=AsyncMock()) as create,
        ):
            created = await auth_module.auth_service.ensure_default_admin(
                session,
                username="admin",
                password="StrongAdminPassword-123!",
                email="admin@example.com",
            )

        self.assertIsNone(created)
        exists_any.assert_awaited_once_with(session)
        create.assert_not_awaited()

    async def test_update_last_login_accepts_aware_datetime(self) -> None:
        session = SimpleNamespace(flush=AsyncMock())
        repository = UserRepository()
        aware_when = datetime.now(UTC)
        user = SimpleNamespace(last_login=None)

        updated = await repository.update_last_login(session, user, aware_when)

        self.assertIs(updated, user)
        self.assertEqual(user.last_login, aware_when)
        session.flush.assert_awaited_once()


class UserRepositoryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self._temp_dir.name) / "users.db"
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

    async def test_create_enforces_case_insensitive_username_uniqueness(self) -> None:
        repository = UserRepository()

        async with self.session_factory() as session:
            await repository.create(
                session,
                username="Admin",
                email="admin@example.com",
                password_hash="hash-1",
            )
            await session.commit()

            with self.assertRaises(IntegrityError):
                await repository.create(
                    session,
                    username="admin",
                    email="other@example.com",
                    password_hash="hash-2",
                )
                await session.commit()


if __name__ == "__main__":
    unittest.main()