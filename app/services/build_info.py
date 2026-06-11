#!/usr/bin/env python3
#
# app/services/build_info.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import logging
import os
from functools import cache
from pathlib import Path

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


@cache
def get_build_info() -> dict[str, str]:
    """Return version and commit metadata for the running build."""
    settings = get_settings()
    base_dir = settings.base_dir
    file_metadata = _read_build_info_file(base_dir / "BUILD_INFO")
    version = os.getenv("APP_VERSION") or _read_text(base_dir / "VERSION") or file_metadata.get("APP_VERSION") or "dev"
    commit = os.getenv("GIT_SHA") or file_metadata.get("GIT_SHA") or "working-tree"
    build_date = os.getenv("BUILD_DATE") or file_metadata.get("BUILD_DATE") or "unknown"
    return {
        "version": version.splitlines()[0],
        "commit": commit.splitlines()[0],
        "build_date": build_date.splitlines()[0],
    }


def _read_text(path: Path) -> str | None:
    """Return stripped file contents, or None for missing, unreadable, or empty files."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Ignoring unreadable build metadata file %s: %s", path, exc)
        return None
    return text or None


def _read_build_info_file(path: Path) -> dict[str, str]:
    """Return parsed key-value metadata from BUILD_INFO."""
    raw_text = _read_text(path)
    if raw_text is None:
        return {}

    metadata: dict[str, str] = {}
    for raw_line in raw_text.splitlines():
        key, separator, value = raw_line.partition("=")
        if not separator:
            continue
        normalized_key = key.strip()
        normalized_value = value.strip()
        if not normalized_key or not normalized_value:
            continue
        metadata[normalized_key] = normalized_value
    return metadata