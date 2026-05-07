#!/usr/bin/env python3
#
# app/utils/parsing.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any


_MAX_JSON_BYTES = 256 * 1024
_MAX_EXPIRY_DAYS = 365 * 100


def split_csv(value: str) -> list[str]:
    """Split a comma-separated string into stripped, non-empty items."""
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_json_object(raw_value: str, field_name: str) -> dict[str, Any]:
    """Parse a JSON object from text with basic size and type validation."""
    if len(raw_value.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ValueError(f"{field_name} exceeds the {_MAX_JSON_BYTES} byte limit.")
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} must be a JSON object.")
    return parsed


def pretty_json(value: dict[str, Any] | list[Any]) -> str:
    """Render a JSON-compatible value as indented, deterministic JSON."""
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def parse_expires_days(raw_value: str | None) -> datetime | None:
    """Convert a day-count string into a capped UTC expiration timestamp."""
    if not raw_value:
        return None
    try:
        days = int(raw_value)
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    return datetime.now(UTC) + timedelta(days=min(days, _MAX_EXPIRY_DAYS))