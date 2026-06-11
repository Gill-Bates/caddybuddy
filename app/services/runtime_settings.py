#!/usr/bin/env python3
#
# app/services/runtime_settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Runtime settings service for database-stored configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.app_settings import DEFAULTS, app_settings_repository


_SIMPLE_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_EMAIL_LENGTH = 255


@dataclass(frozen=True, slots=True)
class CaddyConfig:
    """Caddy-related runtime configuration."""

    admin_url: str
    caddyfile_path: Path | None

    @property
    def caddyfile_path_str(self) -> str:
        """Return caddyfile path as string, or empty if not set."""
        return str(self.caddyfile_path) if self.caddyfile_path else ""


def normalize_caddy_api_url(raw_url: str) -> str:
    """Return a canonical Caddy Admin API URL."""
    normalized = raw_url.strip()
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
    return urlunsplit((parsed.scheme, f"{host}:{port}" if port is not None else host, "", "", ""))


_ALLOWED_CADDYFILE_ROOTS = (Path("/app"), Path("/etc/caddy"), Path("/config"))


def normalize_caddyfile_path(raw_path: str) -> str:
    """Return a validated absolute Caddyfile path.

    Restricts writes to known-safe roots so a compromised admin session cannot
    configure an arbitrary filesystem write target.
    """
    normalized = raw_path.strip()
    if not normalized:
        raise ValueError("Caddyfile path cannot be empty.")
    if "\x00" in normalized:
        raise ValueError("Caddyfile path contains an invalid character.")

    candidate = Path(normalized)
    if not candidate.is_absolute():
        raise ValueError("Caddyfile path must be absolute.")
    if candidate.name != "Caddyfile":
        raise ValueError("Caddyfile path must point to a file named 'Caddyfile'.")

    resolved = candidate.resolve(strict=False)
    if not any(
        resolved == root or resolved.is_relative_to(root)
        for root in _ALLOWED_CADDYFILE_ROOTS
    ):
        allowed = ", ".join(str(r) for r in _ALLOWED_CADDYFILE_ROOTS)
        raise ValueError(
            f"Caddyfile path must be inside an allowed directory ({allowed})."
        )

    return str(candidate)


async def get_caddy_config(session: AsyncSession) -> CaddyConfig:
    """Load and validate Caddy configuration from persisted runtime settings."""
    settings = await app_settings_repository.get_all(session)

    admin_url_value = settings.get("caddy_api_url") or DEFAULTS["caddy_api_url"]
    path_value = settings.get("caddyfile_path") or DEFAULTS.get("caddyfile_path", "")

    admin_url = normalize_caddy_api_url(str(admin_url_value))
    caddyfile_path = Path(normalize_caddyfile_path(str(path_value))) if path_value else None

    return CaddyConfig(admin_url=admin_url, caddyfile_path=caddyfile_path)


async def set_caddy_api_url(session: AsyncSession, url: str) -> None:
    """Stage a normalized Caddy API URL update in the current transaction."""
    await app_settings_repository.set(session, "caddy_api_url", normalize_caddy_api_url(url))


async def set_caddyfile_path(session: AsyncSession, path: str) -> None:
    """Stage a validated Caddyfile path update in the current transaction."""
    await app_settings_repository.set(session, "caddyfile_path", normalize_caddyfile_path(path))


async def set_caddy_config(session: AsyncSession, *, api_url: str, caddyfile_path: str) -> None:
    """Stage Caddy runtime setting updates in the current transaction."""
    await set_caddy_api_url(session, api_url)
    await set_caddyfile_path(session, caddyfile_path)


async def get_rate_limit_enabled(session: AsyncSession) -> bool:
    """Get rate limiting enabled state from database."""
    value = await app_settings_repository.get(session, "rate_limit_enabled")
    return value.lower() not in ("false", "0", "no")


async def set_rate_limit_enabled(session: AsyncSession, enabled: bool) -> None:
    """Update rate limiting enabled state."""
    await app_settings_repository.set(session, "rate_limit_enabled", "true" if enabled else "false")


def normalize_ssllabs_email(raw_email: str) -> str:
    """Return a validated SSL Labs contact email."""
    normalized = raw_email.strip().lower()
    if not normalized:
        return ""
    if len(normalized) > _MAX_EMAIL_LENGTH:
        raise ValueError("ssllabs_email must not exceed 255 characters.")
    if not _SIMPLE_EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("ssllabs_email must be a valid email address.")
    return normalized


async def get_ssllabs_email(session: AsyncSession) -> str | None:
    """Get the persisted SSL Labs email, or None when unset."""
    value = await app_settings_repository.get(session, "ssllabs_email")
    normalized = normalize_ssllabs_email(value)
    return normalized or None


async def set_ssllabs_email(session: AsyncSession, email: str) -> None:
    """Update the persisted SSL Labs email."""
    normalized = normalize_ssllabs_email(email)
    await app_settings_repository.set(session, "ssllabs_email", normalized)
