#!/usr/bin/env python3
#
# app/services/build_info.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import os
from functools import cache
from pathlib import Path

from app.config.settings import get_settings


@cache
def get_build_info() -> dict[str, str]:
    """Return version and commit metadata for the running build."""
    settings = get_settings()
    base_dir = settings.base_dir
    version = os.getenv("APP_VERSION") or _read_text(base_dir / "VERSION") or "dev"
    commit = os.getenv("GIT_SHA") or _read_text(base_dir / "BUILD_INFO") or "working-tree"
    build_date = os.getenv("BUILD_DATE") or "unknown"
    return {
        "version": version.splitlines()[0],
        "commit": commit.splitlines()[0],
        "build_date": build_date.splitlines()[0],
    }


def _read_text(path: Path) -> str | None:
    """Return stripped file contents, or ``None`` for missing or empty files."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return text or None