#!/usr/bin/env python3
#
# app/repositories/configs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import CaddyConfig, CaddyServer


class ConfigRepository:
    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count(CaddyConfig.id)))
        return int(result.scalar_one())

    async def list_all(self, session: AsyncSession) -> list[CaddyConfig]:
        result = await session.execute(
            select(CaddyConfig)
            .options(selectinload(CaddyConfig.servers))
            .order_by(CaddyConfig.updated_at.desc())
        )
        return list(result.scalars().unique().all())

    async def get_by_id(self, session: AsyncSession, config_id: int) -> CaddyConfig | None:
        result = await session.execute(
            select(CaddyConfig)
            .options(selectinload(CaddyConfig.servers))
            .where(CaddyConfig.id == config_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        *,
        name: str,
        json_config: dict,
        status: str,
        metadata_json: dict,
        history_entries: list[dict],
        servers: list[CaddyServer],
    ) -> CaddyConfig:
        config = CaddyConfig(
            name=name,
            json_config=json_config,
            status=status,
            metadata_json=metadata_json,
            history_entries=history_entries,
        )
        config.servers = servers
        session.add(config)
        await session.flush()
        return config

    async def update(
        self,
        session: AsyncSession,
        config: CaddyConfig,
        *,
        name: str,
        json_config: dict,
        status: str,
        metadata_json: dict,
        history_entries: list[dict],
        servers: list[CaddyServer],
    ) -> CaddyConfig:
        config.name = name
        config.json_config = json_config
        config.status = status
        config.metadata_json = metadata_json
        config.history_entries = history_entries
        config.servers = servers
        await session.flush()
        return config

    async def delete(self, session: AsyncSession, config: CaddyConfig) -> None:
        await session.delete(config)


config_repository = ConfigRepository()