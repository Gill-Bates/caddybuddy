#!/usr/bin/env python3
#
# app/utils/version.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Application version for CaddyBuddy."""

from __future__ import annotations

import logging
import tomllib
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def get_version() -> str:
    """Return ``[project] version`` from pyproject.toml, the single source of
    truth for the release version, or 'dev' when it cannot be read.
    """
    path = _PROJECT_ROOT / "pyproject.toml"
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        logger.debug("Unable to read %s: %s", path, exc)
        return "dev"
    except tomllib.TOMLDecodeError as exc:
        logger.warning("pyproject.toml is not valid TOML (%s); version falls back to 'dev'", exc)
        return "dev"

    version = data.get("project", {}).get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()

    logger.warning("pyproject.toml has no [project] version entry")
    return "dev"
