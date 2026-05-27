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
_MAX_JSON_DEPTH = 32
_DEFAULT_MAX_EXPIRY_DAYS = 365


def split_csv(
    value: str,
    *,
    max_items: int = 100,
    max_item_length: int = 255,
) -> list[str]:
    """Split a comma-separated string into stripped, non-empty items."""
    items = [item.strip() for item in value.split(",") if item.strip()]
    if len(items) > max_items:
        raise ValueError(f"CSV value must not contain more than {max_items} items.")
    for item in items:
        if len(item) > max_item_length:
            raise ValueError(f"CSV item exceeds {max_item_length} characters.")
    return items


def _validate_json_depth(value: Any, *, max_depth: int, current_depth: int = 0) -> None:
    if current_depth > max_depth:
        raise ValueError(f"JSON exceeds the maximum nesting depth of {max_depth}.")

    if isinstance(value, dict):
        for item in value.values():
            _validate_json_depth(item, max_depth=max_depth, current_depth=current_depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_json_depth(item, max_depth=max_depth, current_depth=current_depth + 1)


def parse_json_object(raw_value: str, field_name: str) -> dict[str, Any]:
    """Parse a JSON object from text with size, type, and depth validation."""
    if len(raw_value.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ValueError(f"{field_name} exceeds the {_MAX_JSON_BYTES} byte limit.")
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} must be a JSON object.")
    _validate_json_depth(parsed, max_depth=_MAX_JSON_DEPTH)
    return parsed


def pretty_json(value: dict[str, Any] | list[Any]) -> str:
    """Render a JSON-compatible value as indented, deterministic JSON."""
    try:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
    except TypeError as exc:
        raise ValueError("value must be JSON-compatible.") from exc


def parse_expires_days(
    raw_value: str | None,
    *,
    max_days: int = _DEFAULT_MAX_EXPIRY_DAYS,
) -> datetime | None:
    """Convert a day-count string into a UTC expiration timestamp."""
    if raw_value is None or not raw_value.strip():
        return None

    value = raw_value.strip()
    if not value.isdecimal():
        raise ValueError("expires_days must be a positive integer.")

    if max_days <= 0:
        raise ValueError("max_days must be greater than zero.")

    days = int(value)
    if days <= 0:
        raise ValueError("expires_days must be greater than zero.")
    if days > max_days:
        raise ValueError(f"expires_days must not exceed {max_days} days.")

    return datetime.now(UTC) + timedelta(days=days)