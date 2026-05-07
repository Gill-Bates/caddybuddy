#!/usr/bin/env python3
#
# app/repositories/users.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import User


class UserRepository:
    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count(User.id)))
        return int(result.scalar_one())

    async def list_all(self, session: AsyncSession) -> list[User]:
        result = await session.execute(select(User).order_by(User.created_at.desc()))
        return list(result.scalars().all())

    async def get_by_id(self, session: AsyncSession, user_id: int) -> User | None:
        return await session.get(User, user_id)

    async def get_by_username(self, session: AsyncSession, username: str) -> User | None:
        # Case-insensitive username lookup
        result = await session.execute(
            select(User).where(func.lower(User.username) == func.lower(username))
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, session: AsyncSession, email: str) -> User | None:
        result = await session.execute(select(User).where(User.email == email))
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
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            role=role,
            is_active=is_active,
        )
        session.add(user)
        await session.flush()
        return user

    async def update_profile(
        self,
        session: AsyncSession,
        user: User,
        *,
        username: str,
        email: str | None,
    ) -> User:
        user.username = username
        user.email = email
        await session.flush()
        return user

    async def update_password(self, session: AsyncSession, user: User, password_hash: str) -> User:
        user.password_hash = password_hash
        await session.flush()
        return user

    async def update_last_login(self, session: AsyncSession, user: User, when) -> User:
        user.last_login = when
        await session.flush()
        return user


user_repository = UserRepository()