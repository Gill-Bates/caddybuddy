#!/usr/bin/env python3
#
# app/repositories/domains.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Domain


class DomainRepository:
    _PROTECTED_UPDATE_FIELDS = frozenset({"id", "created_at", "updated_at"})

    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count(Domain.id)))
        return int(result.scalar_one())

    async def list_all(self, session: AsyncSession, *, limit: int | None = None) -> list[Domain]:
        statement = select(Domain).order_by(Domain.name.asc())
        if limit is not None:
            statement = statement.limit(limit)
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def get_by_id(self, session: AsyncSession, domain_id: int) -> Domain | None:
        return await session.get(Domain, domain_id)

    async def get_by_name(self, session: AsyncSession, name: str) -> Domain | None:
        result = await session.execute(select(Domain).where(Domain.name == name))
        return result.scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        *,
        name: str,
        server_id: int | None,
        upstream: str | None,
        caddy_directives: str | None,
        ssl_enabled: bool,
        ssl_provider: str,
        active: bool,
        description: str | None,
    ) -> Domain:
        domain = Domain(
            name=name,
            server_id=server_id,
            upstream=upstream,
            caddy_directives=caddy_directives,
            ssl_enabled=ssl_enabled,
            ssl_provider=ssl_provider,
            active=active,
            description=description,
        )
        session.add(domain)
        await session.flush()
        return domain

    async def update(self, session: AsyncSession, domain: Domain, **updates: Any) -> Domain:
        for field_name, value in updates.items():
            if field_name in self._PROTECTED_UPDATE_FIELDS or not hasattr(domain, field_name):
                continue
            setattr(domain, field_name, value)
        await session.flush()
        return domain

    async def delete(self, session: AsyncSession, domain: Domain) -> None:
        await session.delete(domain)
        await session.flush()


domain_repository = DomainRepository()
