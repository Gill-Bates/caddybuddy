#!/usr/bin/env python3
#
# app/services/build_info.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from functools import cache
from pathlib import Path

from app.config.settings import get_settings


@cache
def get_build_info() -> dict[str, str]:
    """Return version, commit, and environment metadata for the running build."""
    settings = get_settings()
    base_dir = settings.base_dir
    return {
        "version": _read_text(base_dir / "VERSION") or "dev",
        "commit": (_read_text(base_dir / "BUILD_INFO") or "working-tree").splitlines()[0],
        "environment": settings.environment,
    }


def _read_text(path: Path) -> str | None:
    """Return stripped file contents, or ``None`` for missing or empty files."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return text or None