#!/usr/bin/env python3
#
# app/repositories/app_settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AppSetting


# Defaults used when no database entry exists
DEFAULTS: dict[str, str] = {
    "caddy_api_url": "http://localhost:2019",
    "caddyfile_path": "/etc/caddy/Caddyfile",
}


class AppSettingsRepository:
    async def get(self, session: AsyncSession, key: str) -> str:
        """Get a setting value, returning default if not set."""
        result = await session.execute(
            select(AppSetting.value).where(AppSetting.key == key)
        )
        value = result.scalar_one_or_none()
        return value if value is not None else DEFAULTS.get(key, "")

    async def get_all(self, session: AsyncSession) -> dict[str, str]:
        """Get all settings as a dict, with defaults for missing keys."""
        result = await session.execute(select(AppSetting.key, AppSetting.value))
        stored = {row.key: row.value for row in result.all()}
        return {**DEFAULTS, **stored}

    async def set(self, session: AsyncSession, key: str, value: str) -> AppSetting:
        """Set a setting value, creating or updating as needed."""
        result = await session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        setting = result.scalar_one_or_none()

        if setting is None:
            setting = AppSetting(key=key, value=value)
            session.add(setting)
        else:
            setting.value = value

        await session.flush()
        return setting

    async def delete(self, session: AsyncSession, key: str) -> bool:
        """Delete a setting, reverting to default. Returns True if existed."""
        result = await session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        setting = result.scalar_one_or_none()
        if setting:
            await session.delete(setting)
            await session.flush()
            return True
        return False


app_settings_repository = AppSettingsRepository()
