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
from app.utils.admin_targets import validate_admin_host


_SIMPLE_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_UNSAFE_CADDY_API_URL_PATTERN = re.compile(r"[\x00-\x1f\x7f\\]")
_MAX_EMAIL_LENGTH = 255
_MAX_CADDY_API_URL_LENGTH = 2048


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
    if len(normalized) > _MAX_CADDY_API_URL_LENGTH:
        raise ValueError("caddy_api_url must not exceed 2048 characters.")
    if _UNSAFE_CADDY_API_URL_PATTERN.search(normalized):
        raise ValueError("caddy_api_url contains an invalid character.")

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
    if port is None:
        raise ValueError("caddy_api_url must include an explicit port (for example :2019).")

    host = parsed.hostname
    if host is None:
        raise ValueError("caddy_api_url must include a host.")
    try:
        validate_admin_host(host)
    except ValueError as exc:
        raise ValueError(f"caddy_api_url host is not allowed: {host.strip().lower()!r}") from exc

    if ":" in host:
        host = f"[{host}]"
    return urlunsplit((parsed.scheme, f"{host}:{port}", "", "", ""))


_ALLOWED_CADDYFILE_ROOTS = tuple(
    root.resolve(strict=False)
    for root in (
        Path("/app"),
        Path("/config"),
        Path("/etc/caddy"),
        Path("/etc/opt/caddy"),
        Path("/usr/local/etc/caddy"),
    )
)

_HOST_CADDYFILE_HINTS = (
    Path("/etc/caddy/Caddyfile"),
    Path("/usr/local/etc/caddy/Caddyfile"),
    Path("/etc/opt/caddy/Caddyfile"),
)
_CONTAINER_CADDYFILE_HINTS = (
    Path("/app/Caddyfile"),
    Path("/config/Caddyfile"),
    Path("/etc/caddy/Caddyfile"),
)


def _dedupe_caddyfile_candidates(paths: tuple[Path, ...] | list[Path]) -> tuple[Path, ...]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.resolve(strict=False)
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return tuple(unique)


def discover_caddyfile_candidates(
    runtime_location: str | None,
    *,
    mounted_caddyfile_path: Path | None = None,
) -> tuple[Path, ...]:
    """Return likely Caddyfile paths in preferred order.

    The order intentionally differs by runtime location so onboarding can
    prefill the most likely path and still show alternatives for broad Linux
    installations.
    """
    normalized_runtime = runtime_location.strip().lower() if isinstance(runtime_location, str) else ""

    candidates: list[Path] = []
    if normalized_runtime == "container":
        if mounted_caddyfile_path is not None:
            candidates.append(mounted_caddyfile_path)
        candidates.extend(_CONTAINER_CADDYFILE_HINTS)
    elif normalized_runtime == "host":
        candidates.extend(_HOST_CADDYFILE_HINTS)
    else:
        if mounted_caddyfile_path is not None:
            candidates.append(mounted_caddyfile_path)
        candidates.extend(_HOST_CADDYFILE_HINTS)
        candidates.extend(_CONTAINER_CADDYFILE_HINTS)

    return _dedupe_caddyfile_candidates(candidates)


def suggest_caddyfile_path(
    runtime_location: str | None,
    *,
    mounted_caddyfile_path: Path | None = None,
) -> str:
    """Return the first existing candidate Caddyfile path, or the best guess."""
    candidates = discover_caddyfile_candidates(
        runtime_location,
        mounted_caddyfile_path=mounted_caddyfile_path,
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return str(candidates[0]) if candidates else ""


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
    # Re-check the name after resolution so a symlink named "Caddyfile" cannot
    # redirect the persisted write target to a differently named file.
    if resolved.name != "Caddyfile":
        raise ValueError("Caddyfile path must resolve to a file named 'Caddyfile'.")
    if not any(
        resolved == root or resolved.is_relative_to(root)
        for root in _ALLOWED_CADDYFILE_ROOTS
    ):
        allowed = ", ".join(str(r) for r in _ALLOWED_CADDYFILE_ROOTS)
        raise ValueError(
            f"Caddyfile path must be inside an allowed directory ({allowed})."
        )

    return str(resolved)


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


async def clear_caddyfile_path(session: AsyncSession) -> None:
    """Stage clearing the Caddyfile path so persistence falls back to unset."""
    await app_settings_repository.set(session, "caddyfile_path", "")


async def set_caddy_config(session: AsyncSession, *, api_url: str, caddyfile_path: str) -> None:
    """Stage Caddy runtime setting updates in the current transaction."""
    normalized_api_url = normalize_caddy_api_url(api_url)
    normalized_caddyfile_path = normalize_caddyfile_path(caddyfile_path)
    await app_settings_repository.set(session, "caddy_api_url", normalized_api_url)
    await app_settings_repository.set(session, "caddyfile_path", normalized_caddyfile_path)


async def get_rate_limit_enabled(session: AsyncSession) -> bool:
    """Get rate limiting enabled state from database."""
    value = await app_settings_repository.get(session, "rate_limit_enabled")
    normalized = str(value or DEFAULTS["rate_limit_enabled"]).strip().lower()
    return normalized not in {"false", "0", "no", "off"}


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
    normalized = normalize_ssllabs_email(str(value or ""))
    return normalized or None


async def set_ssllabs_email(session: AsyncSession, email: str) -> None:
    """Update the persisted SSL Labs email."""
    normalized = normalize_ssllabs_email(email)
    await app_settings_repository.set(session, "ssllabs_email", normalized)


# Allowed retention windows (days) for the SSL Labs rank-history table, exposed to the
# Settings slider. Ordered ascending; the largest value is the default.
SSLLABS_RETENTION_DAY_VALUES: tuple[int, ...] = (30, 90, 180, 365)
SSLLABS_RETENTION_DEFAULT_DAYS = 365


async def get_ssllabs_history_retention_days(session: AsyncSession) -> int:
    """Get the SSL Labs rank-history retention window in days.

    Falls back to the default and snaps to the nearest allowed value so a malformed or
    out-of-range stored value can never disable or unbound pruning.
    """
    raw = await app_settings_repository.get(session, "ssllabs_history_retention_days")
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        return SSLLABS_RETENTION_DEFAULT_DAYS
    if days in SSLLABS_RETENTION_DAY_VALUES:
        return days
    return min(SSLLABS_RETENTION_DAY_VALUES, key=lambda allowed: abs(allowed - days))


async def set_ssllabs_history_retention_days(session: AsyncSession, days: int) -> None:
    """Update the SSL Labs rank-history retention window."""
    if days not in SSLLABS_RETENTION_DAY_VALUES:
        allowed = ", ".join(str(value) for value in SSLLABS_RETENTION_DAY_VALUES)
        raise ValueError(f"ssllabs_history_retention_days must be one of: {allowed}.")
    await app_settings_repository.set(session, "ssllabs_history_retention_days", str(days))
