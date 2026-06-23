#!/usr/bin/env python3
#
# app/services/about.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""About page data: runtime metadata, dependencies, changelog, and GitHub update checks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

import httpx
import markdown as _markdown
import nh3

from app.config.settings import get_settings
from app.services.build_info import get_build_info

logger = logging.getLogger(__name__)

GITHUB_REPO = "Gill-Bates/caddybuddy"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Dependencies surfaced on the About page (PyPA-normalized names resolved below).
_KEY_PACKAGES = (
    "aiosqlite", "bcrypt", "cryptography", "fastapi", "httpx", "itsdangerous",
    "jinja2", "markdown", "nh3", "pydantic", "pydantic-settings",
    "python-multipart", "slowapi", "sqlalchemy", "uvicorn",
)

# Allowed HTML tags/attrs for sanitized changelog rendering.
_CHANGELOG_ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr",
    "ul", "ol", "li", "a", "strong", "em", "b", "i",
    "code", "pre", "blockquote", "table", "thead", "tbody",
    "tr", "th", "td", "dl", "dt", "dd", "abbr", "sup", "sub",
    "details", "summary",
}
_CHANGELOG_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "abbr": {"title"},
    "details": {"open"},
}

# Update-check result is cached for one hour to avoid hammering the GitHub API.
_UPDATE_CHECK_TTL_SECONDS = 3600
_UPDATE_CHECK_TIMEOUT_SECONDS = 10.0
_MAX_RELEASE_NOTES = 20_000
_update_check_cache: UpdateInfo | None = None
_update_check_time: float = 0.0
_update_check_lock = threading.Lock()


class UpdateInfo(TypedDict):
    """GitHub release update-check result."""

    update_available: bool
    current_version: str
    latest_version: str | None
    release_url: str | None
    published_at: str | None
    error: str | None


def _normalize_pkg_name(name: str) -> str:
    """Normalize a package name per the PyPA name-normalization spec."""
    return re.sub(r"[-_.]+", "-", str(name or "").strip().lower())


def _parse_requirements() -> dict[str, str]:
    """Parse requirements.txt into a normalized package -> version mapping."""
    req_path = get_settings().base_dir / "requirements.txt"
    try:
        raw = req_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read requirements.txt: %s", exc)
        return {}

    versions: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        for separator in ("==", ">=", "<=", "~=", "!=", ">", "<"):
            if separator in line:
                pkg, ver = line.split(separator, 1)
                pkg = _normalize_pkg_name(pkg.split("[", 1)[0])
                ver = ver.split(",", 1)[0].split("#", 1)[0].split(";", 1)[0].strip()
                versions[pkg] = ver
                break
    return versions


def _resolve_dependencies(requirements_versions: dict[str, str]) -> list[tuple[str, str]]:
    """Resolve display versions for the key packages, preferring installed metadata."""
    from importlib.metadata import PackageNotFoundError, version as get_pkg_version

    dependencies: list[tuple[str, str]] = []
    for pkg_name in _KEY_PACKAGES:
        try:
            resolved = get_pkg_version(pkg_name)
        except PackageNotFoundError:
            resolved = requirements_versions.get(_normalize_pkg_name(pkg_name), "?")
        dependencies.append((pkg_name, resolved))
    dependencies.sort(key=lambda item: item[0].lower())
    return dependencies


def _render_changelog() -> str:
    """Render CHANGELOG.md to sanitized HTML."""
    changelog_path = get_settings().base_dir / "CHANGELOG.md"
    try:
        raw = changelog_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "<p>Changelog not found.</p>"
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read changelog: %s", exc)
        return "<p>Failed to read changelog.</p>"

    try:
        rendered = _markdown.markdown(raw, extensions=["extra", "sane_lists"])
    except (ValueError, TypeError, RecursionError) as exc:
        logger.warning("Failed to render changelog: %s", exc)
        return "<p>Changelog could not be rendered.</p>"

    return nh3.clean(
        rendered,
        tags=_CHANGELOG_ALLOWED_TAGS,
        attributes=_CHANGELOG_ALLOWED_ATTRS,
        url_schemes={"http", "https", "mailto"},
        strip_comments=True,
    )


def _get_configured_timezone() -> str:
    """Resolve the display timezone: TZ env, /etc/timezone, /etc/localtime, else local time."""
    tz_name = os.getenv("TZ", "").strip()
    if tz_name:
        return tz_name

    try:
        tz_text = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if tz_text:
            return tz_text
    except OSError:
        pass

    try:
        localtime_path = Path("/etc/localtime").resolve()
        zoneinfo_root = Path("/usr/share/zoneinfo")
        if zoneinfo_root in localtime_path.parents:
            return str(localtime_path.relative_to(zoneinfo_root))
    except OSError:
        pass

    return "System local time"


@lru_cache(maxsize=1)
def _get_about_data() -> dict[str, object]:
    """Build and cache About page data for the process lifetime.

    Performs blocking file reads; always reach it via :func:`get_about_data` so the
    event loop is not blocked and concurrent first-load requests do not duplicate work.
    Callers must treat the returned mapping as read-only.
    """
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    build_info = get_build_info()
    return {
        "version": build_info["version"],
        "commit": build_info["commit"],
        "python_version": python_version,
        "timezone": _get_configured_timezone(),
        "dependencies": _resolve_dependencies(_parse_requirements()),
        "changelog_html": _render_changelog(),
    }


_about_data_lock = asyncio.Lock()


async def get_about_data() -> dict[str, object]:
    """Return a fresh copy of cached About page data without blocking the event loop."""
    if _get_about_data.cache_info().currsize > 0:
        data = _get_about_data()
    else:
        async with _about_data_lock:
            data = await asyncio.to_thread(_get_about_data)
    return {**data, "dependencies": list(data["dependencies"])}


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string ('1.2.3', 'v1.2.3', '1.2.3-beta') to a comparable tuple."""
    match = re.match(r"^(\d+(?:\.\d+)*)", (version_str or "").lstrip("v").strip())
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split("."))


def _is_newer_version(current: str, latest: str) -> bool:
    """Return True when ``latest`` is a newer release than ``current``."""
    if current == "dev":
        return False
    return _parse_version(latest) > _parse_version(current)


def _check_for_updates_blocking(force: bool) -> UpdateInfo:
    """Query GitHub for the latest release (blocking; cached for one hour)."""
    global _update_check_cache, _update_check_time

    current_version = get_build_info()["version"]
    now = time.monotonic()
    with _update_check_lock:
        if not force and _update_check_cache is not None and now - _update_check_time < _UPDATE_CHECK_TTL_SECONDS:
            return _update_check_cache

    result: UpdateInfo = {
        "update_available": False,
        "current_version": current_version,
        "latest_version": None,
        "release_url": None,
        "published_at": None,
        "error": None,
    }

    if current_version == "dev":
        result["error"] = "Development version - update check disabled"
    else:
        try:
            with httpx.Client(timeout=httpx.Timeout(_UPDATE_CHECK_TIMEOUT_SECONDS)) as client:
                response = client.get(
                    GITHUB_API_URL,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": f"CaddyBuddy/{current_version}",
                    },
                    follow_redirects=True,
                )
                response.raise_for_status()
                data = response.json()

            latest_tag = str(data.get("tag_name", "")).lstrip("v")
            result["latest_version"] = latest_tag
            result["release_url"] = data.get("html_url")
            result["published_at"] = data.get("published_at")
            if latest_tag and _is_newer_version(current_version, latest_tag):
                result["update_available"] = True
                logger.info("Update available: %s -> %s", current_version, latest_tag)
        except httpx.HTTPStatusError as exc:
            result["error"] = f"GitHub API error: {exc.response.status_code}"
            logger.warning("Update check failed: %s", exc)
        except httpx.TimeoutException:
            result["error"] = "Connection timeout"
        except httpx.RequestError as exc:
            result["error"] = f"Network error: {exc}"
        except (json.JSONDecodeError, ValueError) as exc:
            result["error"] = f"Invalid response: {exc}"

    with _update_check_lock:
        _update_check_cache = result
        _update_check_time = time.monotonic()
    return result


async def check_for_updates(force: bool = False) -> UpdateInfo:
    """Return GitHub update-check info without blocking the event loop."""
    return await asyncio.to_thread(_check_for_updates_blocking, force)
