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
from urllib.parse import urlsplit, urlunsplit

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
    normalized = url.strip()
    if not normalized:
        raise ValueError("Caddy API URL cannot be empty")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("caddy_api_url must use http or https.")
    if parsed.username or parsed.password:
        raise ValueError("caddy_api_url must not include username or password.")
    if not parsed.hostname:
        raise ValueError("caddy_api_url must include a host.")
    if parsed.path not in {"", "/"}:
        raise ValueError("caddy_api_url must not include a path.")
    if parsed.query or parsed.fragment:
        raise ValueError("caddy_api_url must not include query or fragment.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("caddy_api_url has an invalid port.") from exc

    host = parsed.hostname
    if host is None:
        raise ValueError("caddy_api_url must include a host.")
    if ":" in host:
        host = f"[{host}]"
    normalized = urlunsplit((parsed.scheme, f"{host}:{port}" if port is not None else host, "", "", ""))
    await app_settings_repository.set(session, "caddy_api_url", normalized)


async def set_caddyfile_path(session: AsyncSession, path: str) -> None:
    """Update the Caddyfile path setting."""
    normalized = path.strip()
    if not normalized:
        raise ValueError("Caddyfile path cannot be empty")
    await app_settings_repository.set(session, "caddyfile_path", normalized)


async def set_caddy_config(session: AsyncSession, *, api_url: str, caddyfile_path: str) -> None:
    await set_caddy_api_url(session, api_url)
    await set_caddyfile_path(session, caddyfile_path)


async def get_rate_limit_enabled(session: AsyncSession) -> bool:
    """Get rate limiting enabled state from database."""
    value = await app_settings_repository.get(session, "rate_limit_enabled")
    return value.lower() not in ("false", "0", "no")


async def set_rate_limit_enabled(session: AsyncSession, enabled: bool) -> None:
    """Update rate limiting enabled state."""
    await app_settings_repository.set(session, "rate_limit_enabled", "true" if enabled else "false")
