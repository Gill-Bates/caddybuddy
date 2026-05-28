#!/usr/bin/env python3
#
# app/utils/ssllabs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address

from app.schemas.ssllabs import SslLabsScanStatus, SslLabsScheduleFrequency


_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.ASCII)
_BLOCKED_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".home",
    ".home.arpa",
    ".test",
    ".invalid",
    ".example",
)
_BLOCKED_HOSTS = {"localhost"}


def validate_ssllabs_host(host: str) -> str:
    normalized = host.strip().rstrip(".").casefold()
    if not normalized or "*" in normalized:
        raise ValueError("SSL Labs scans require a concrete public hostname.")
    if any(char.isspace() for char in normalized):
        raise ValueError("SSL Labs scans require a hostname without whitespace.")
    if any(token in normalized for token in ("://", "/", "\\", "@", ":")):
        raise ValueError("SSL Labs scans require a hostname, not a URL.")
    if normalized in _BLOCKED_HOSTS or normalized.endswith(_BLOCKED_SUFFIXES):
        raise ValueError("SSL Labs scans require a public hostname.")

    try:
        ip_address(normalized)
    except ValueError:
        pass
    else:
        raise ValueError("SSL Labs scans require a public hostname.")

    labels = normalized.split(".")
    if len(labels) < 2 or any(not label for label in labels):
        raise ValueError("SSL Labs scans require a public hostname.")
    if not all(_HOST_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError("SSL Labs scans require a valid public hostname.")
    return normalized


def mask_email(email: str | None) -> str | None:
    if email is None:
        return None
    local_part, separator, domain = email.partition("@")
    if not separator or not local_part or not domain:
        return "configured"
    return f"{local_part[:1]}***@{domain}"


def schedule_interval(frequency: SslLabsScheduleFrequency) -> timedelta:
    if frequency == "weekly":
        return timedelta(days=7)
    if frequency == "monthly":
        return timedelta(days=30)
    raise ValueError(f"Unsupported SSL Labs schedule frequency: {frequency!r}")


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _deterministic_jitter_seconds(key: str, max_jitter_seconds: int) -> int:
    if max_jitter_seconds <= 0:
        return 0
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (max_jitter_seconds + 1)


def next_schedule_time(
    frequency: SslLabsScheduleFrequency,
    reference: datetime,
    *,
    jitter_key: str | None = None,
    max_jitter: timedelta | None = None,
) -> datetime:
    scheduled = ensure_aware_utc(reference) + schedule_interval(frequency)
    if jitter_key is None or max_jitter is None:
        return scheduled

    max_jitter_seconds = max(int(max_jitter.total_seconds()), 0)
    if max_jitter_seconds == 0:
        return scheduled

    jitter_seconds = _deterministic_jitter_seconds(f"{frequency}:{jitter_key}", max_jitter_seconds)
    return scheduled + timedelta(seconds=jitter_seconds)


def grade_badge_class(grade: str | None) -> str:
    """Return Bootstrap badge class based on SSL Labs grade."""
    normalized = (grade or "").upper().strip()
    if not normalized:
        return "bg-secondary"
    # A+, A, A- = green (success)
    if normalized.startswith("A"):
        return "bg-success"
    # B = yellow (warning)
    if normalized.startswith("B"):
        return "bg-warning text-dark"
    # C, D, E, F, T (trust issues) = red (danger)
    return "bg-danger"


def status_badge_class(status: SslLabsScanStatus | str | None, grade: str | None = None) -> str:
    """Return status pill class for scan status (used for overall status display)."""
    normalized_status = (status or "").lower()
    normalized_grade = (grade or "").upper()
    if normalized_status in {"error", "failed"}:
        return "status-pill--offline"
    if normalized_status in {"queued", "starting", "dns", "in_progress", "rate_limited"}:
        return "status-pill--unknown"
    if normalized_status == "ready" and normalized_grade.startswith("A"):
        return "status-pill--online"
    if normalized_status == "ready" and normalized_grade.startswith("B"):
        return "status-pill--warning"
    if normalized_status == "ready":
        return "status-pill--offline"  # C, D, E, F grades
    return "status-pill--unknown"


def extract_endpoint_details(result_json: dict | None) -> list[dict]:
    """Extract endpoint details (IP, grade, protocol) from SSL Labs result JSON."""
    if not result_json or not isinstance(result_json, dict):
        return []

    endpoints = result_json.get("endpoints")
    if not isinstance(endpoints, list):
        return []

    details = []
    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        ip = ep.get("ipAddress", "")
        grade = ep.get("grade") or ep.get("gradeTrustIgnored") or ""
        status_msg = ep.get("statusMessage", "")

        # Determine if IPv4 or IPv6
        ip_version = "IPv6" if ":" in ip else "IPv4"

        details.append({
            "ip": ip,
            "ip_version": ip_version,
            "grade": grade,
            "status": status_msg,
            "badge_class": grade_badge_class(grade),
        })

    return details