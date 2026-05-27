#!/usr/bin/env python3
#
# app/services/runtime_settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""
Runtime settings service providing cached access to database-stored configuration.

These settings were previously environment variables but are now managed via the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.app_settings import DEFAULTS, app_settings_repository


@dataclass(frozen=True, slots=True)
class CaddyConfig:
    """Caddy-related runtime configuration."""

    admin_url: str
    caddyfile_path: Path | None

    @property
    def caddyfile_path_str(self) -> str:
        """Return caddyfile path as string, or empty if not set."""
        return str(self.caddyfile_path) if self.caddyfile_path else ""


async def get_caddy_config(session: AsyncSession) -> CaddyConfig:
    """
    Load Caddy configuration from database settings.

    Returns:
        CaddyConfig with admin_url and caddyfile_path from database,
        falling back to defaults if not set.
    """
    settings = await app_settings_repository.get_all(session)

    admin_url = settings.get("caddy_api_url", DEFAULTS["caddy_api_url"])

    path_str = settings.get("caddyfile_path", DEFAULTS["caddyfile_path"])
    caddyfile_path = Path(path_str) if path_str else None

    return CaddyConfig(admin_url=admin_url, caddyfile_path=caddyfile_path)


async def set_caddy_api_url(session: AsyncSession, url: str) -> None:
    """Update the Caddy API URL setting."""
    # Normalize: strip whitespace and trailing slashes
    normalized = url.strip().rstrip("/")
    if not normalized:
        raise ValueError("Caddy API URL cannot be empty")
    await app_settings_repository.set(session, "caddy_api_url", normalized)


async def set_caddyfile_path(session: AsyncSession, path: str) -> None:
    """Update the Caddyfile path setting."""
    normalized = path.strip()
    if not normalized:
        raise ValueError("Caddyfile path cannot be empty")
    await app_settings_repository.set(session, "caddyfile_path", normalized)
