#!/usr/bin/env python3
#
# app/repositories/servers.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import CaddyServer


class ServerRepository:
    _PROTECTED_UPDATE_FIELDS = frozenset({"id", "created_at", "updated_at"})

    def _base_select(self):
        return select(CaddyServer).options(selectinload(CaddyServer.configs))

    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count(CaddyServer.id)))
        return int(result.scalar_one())

    async def list_all(
        self, session: AsyncSession, *, active_only: bool = False, limit: int | None = None
    ) -> list[CaddyServer]:
        statement = self._base_select()
        if active_only:
            statement = statement.where(CaddyServer.active.is_(True))
        statement = statement.order_by(CaddyServer.created_at.desc())
        if limit is not None:
            statement = statement.limit(limit)
        result = await session.execute(statement)
        return list(result.scalars().unique().all())

    async def get_by_id(self, session: AsyncSession, server_id: int) -> CaddyServer | None:
        result = await session.execute(
            self._base_select().where(CaddyServer.id == server_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        *,
        name: str,
        api_url: str,
        api_port: int,
        admin_api_path: str,
        active: bool,
        tags: list[str],
        status: str,
    ) -> CaddyServer:
        server = CaddyServer(
            name=name,
            api_url=api_url,
            api_port=api_port,
            admin_api_path=admin_api_path,
            active=active,
            tags=tags,
            status=status,
        )
        session.add(server)
        await session.flush()
        return server

    async def update(self, session: AsyncSession, server: CaddyServer, **updates) -> CaddyServer:
        for field_name, value in updates.items():
            if field_name in self._PROTECTED_UPDATE_FIELDS or not hasattr(server, field_name):
                continue
            setattr(server, field_name, value)
        await session.flush()
        return server

    async def delete(self, session: AsyncSession, server: CaddyServer) -> None:
        await session.delete(server)
        await session.flush()


server_repository = ServerRepository()