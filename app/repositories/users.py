#!/usr/bin/env python3
#
# app/repositories/users.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import User


class UserRepository:
    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count()).select_from(User))
        return int(result.scalar_one())

    async def exists_any(self, session: AsyncSession) -> bool:
        result = await session.execute(select(User.id).limit(1))
        return result.scalar_one_or_none() is not None

    async def get_by_id(self, session: AsyncSession, user_id: int) -> User | None:
        return await session.get(User, user_id)

    async def get_by_username(self, session: AsyncSession, username: str) -> User | None:
        # Case-insensitive username lookup
        normalized_username = username.strip().lower()
        result = await session.execute(
            select(User).where(func.lower(User.username) == normalized_username)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        *,
        username: str,
        email: str | None,
        password_hash: str,
        role: str = "user",
        is_active: bool = True,
    ) -> User:
        normalized_username = username.strip()
        normalized_email = email.strip().lower() if isinstance(email, str) else None

        if not normalized_username:
            raise ValueError("Username must not be empty.")

        user = User(
            username=normalized_username,
            email=normalized_email,
            password_hash=password_hash,
            role=role,
            is_active=is_active,
        )
        session.add(user)
        await session.flush()
        return user

    async def update_last_login(
        self,
        session: AsyncSession,
        user: User,
        when: datetime,
    ) -> User:
        if when.tzinfo is None:
            raise ValueError("last_login must be timezone-aware.")
        user.last_login = when
        await session.flush()
        return user

    async def update_password(
        self,
        session: AsyncSession,
        user: User,
        password_hash: str,
    ) -> User:
        """Update user's password hash."""
        user.password_hash = password_hash
        await session.flush()
        return user


user_repository = UserRepository()