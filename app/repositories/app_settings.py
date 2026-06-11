#!/usr/bin/env python3
#
# app/repositories/app_settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import DEFAULT_CADDY_ADMIN_URL, DEFAULT_CADDYFILE_PATH
from app.models.entities import AppSetting


# Defaults used when no database entry exists
DEFAULTS: dict[str, str] = {
    "caddy_api_url": DEFAULT_CADDY_ADMIN_URL,
    "caddyfile_path": str(DEFAULT_CADDYFILE_PATH),
    "rate_limit_enabled": "true",
    "ssllabs_email": "",
}


def _validate_setting_key(key: str) -> str:
    normalized = key.strip().lower()
    if normalized not in DEFAULTS:
        raise ValueError(f"Unknown app setting key: {key!r}")
    return normalized


def _upsert_app_setting(key: str, value: str, dialect_name: str):
    if dialect_name == "postgresql":
        stmt = postgres_insert(AppSetting)
    else:
        stmt = sqlite_insert(AppSetting)
    return stmt.values(key=key, value=value).on_conflict_do_update(
        index_elements=[AppSetting.key],
        set_={"value": value},
    )


class AppSettingsRepository:
    async def get(self, session: AsyncSession, key: str) -> str:
        """Get a setting value, returning default if not set."""
        normalized_key = _validate_setting_key(key)
        result = await session.execute(
            select(AppSetting.value).where(AppSetting.key == normalized_key)
        )
        value = result.scalar_one_or_none()
        return value if value is not None else DEFAULTS[normalized_key]

    async def get_all(self, session: AsyncSession) -> dict[str, str]:
        """Get all settings as a dict, with defaults for missing keys."""
        result = await session.execute(select(AppSetting.key, AppSetting.value))
        stored = {row.key: row.value for row in result.all() if row.key in DEFAULTS}
        return {**DEFAULTS, **stored}

    async def set(self, session: AsyncSession, key: str, value: str) -> AppSetting:
        """Set a setting value, creating or updating as needed."""
        normalized_key = _validate_setting_key(key)
        dialect_name = session.get_bind().dialect.name
        await session.execute(_upsert_app_setting(normalized_key, value, dialect_name))
        await session.flush()
        result = await session.execute(
            select(AppSetting).where(AppSetting.key == normalized_key)
        )
        setting = result.scalar_one()
        return setting

    async def delete(self, session: AsyncSession, key: str) -> bool:
        """Delete a setting, reverting to default. Returns True if existed."""
        normalized_key = _validate_setting_key(key)
        result = await session.execute(
            select(AppSetting).where(AppSetting.key == normalized_key)
        )
        setting = result.scalar_one_or_none()
        if setting:
            await session.delete(setting)
            await session.flush()
            return True
        return False


app_settings_repository = AppSettingsRepository()
