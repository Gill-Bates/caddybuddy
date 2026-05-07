#!/usr/bin/env python3
#
# app/repositories/api_keys.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import ApiKey


class ApiKeyRepository:
    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count(ApiKey.id)))
        return int(result.scalar_one())

    async def list_all(self, session: AsyncSession) -> list[ApiKey]:
        statement = select(ApiKey).options(selectinload(ApiKey.user)).order_by(ApiKey.created_at.desc())
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def list_for_user(self, session: AsyncSession, user_id: int | None = None) -> list[ApiKey]:
        statement = select(ApiKey).options(selectinload(ApiKey.user)).order_by(ApiKey.created_at.desc())
        if user_id is not None:
            statement = statement.where(ApiKey.user_id == user_id)
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def get_by_id(self, session: AsyncSession, api_key_id: int) -> ApiKey | None:
        result = await session.execute(
            select(ApiKey)
            .options(selectinload(ApiKey.user))
            .where(ApiKey.id == api_key_id)
        )
        return result.scalar_one_or_none()

    async def get_by_hash(self, session: AsyncSession, key_hash: str) -> ApiKey | None:
        result = await session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        return result.scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        *,
        name: str,
        key_prefix: str,
        key_hash: str,
        user_id: int,
        permissions: dict,
        expires_at,
    ) -> ApiKey:
        api_key = ApiKey(
            name=name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            user_id=user_id,
            permissions=permissions,
            expires_at=expires_at,
        )
        session.add(api_key)
        await session.flush()
        return api_key

    async def set_active(self, session: AsyncSession, api_key: ApiKey, is_active: bool) -> ApiKey:
        api_key.is_active = is_active
        await session.flush()
        return api_key

    async def mark_used(self, session: AsyncSession, api_key: ApiKey, when) -> ApiKey:
        api_key.last_used = when
        await session.flush()
        return api_key


api_key_repository = ApiKeyRepository()