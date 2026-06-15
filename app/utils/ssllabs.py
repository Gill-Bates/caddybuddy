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

from app.schemas.ssllabs import (
    SSLLABS_ACTIVE_SCAN_STATUSES,
    SSLLABS_FAILED_SCAN_STATUSES,
    SSLLABS_SCHEDULE_FREQUENCIES,
    SSLLABS_TERMINAL_SCAN_STATUSES,
    SslLabsScanStatus,
    SslLabsScheduleFrequency,
)


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


def normalize_ssllabs_schedule_frequency(raw_value: str | None) -> SslLabsScheduleFrequency | None:
    """Normalize the persisted SSL Labs scheduler value.

    Scheduling is intentionally On/Off: when enabled, scans run weekly. No other
    frequency is valid application state.
    """
    if raw_value is None:
        return None
    normalized = raw_value.strip().lower()
    if not normalized:
        return None
    if normalized in SSLLABS_SCHEDULE_FREQUENCIES:
        return normalized
    raise ValueError("invalid ssllabs schedule frequency")


def parse_ssllabs_schedule_control(raw_value: str) -> SslLabsScheduleFrequency | None:
    """Map the UI/API On/Off scheduler control to the persisted frequency."""
    normalized = raw_value.strip().lower()
    if not normalized or normalized in {"off", "false", "0", "no"}:
        return None
    if normalized in {"on", "weekly", "true", "1", "yes"}:
        return "weekly"
    raise ValueError("Invalid SSL Labs schedule value.")


def schedule_interval(frequency: SslLabsScheduleFrequency) -> timedelta:
    if frequency == "weekly":
        return timedelta(days=7)
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


def _advance_schedule_base(frequency: SslLabsScheduleFrequency, reference: datetime) -> datetime:
    if frequency == "weekly":
        return reference + schedule_interval(frequency)
    raise ValueError(f"Unsupported SSL Labs schedule frequency: {frequency!r}")


def next_schedule_time(
    frequency: SslLabsScheduleFrequency,
    reference: datetime,
    *,
    jitter_key: str | None = None,
    max_jitter: timedelta | None = None,
    minimum_after: datetime | None = None,
    reference_includes_jitter: bool = False,
) -> datetime:
    base_reference = ensure_aware_utc(reference)
    minimum_after_utc = ensure_aware_utc(minimum_after) if minimum_after is not None else None

    jitter_seconds = 0
    if jitter_key is not None and max_jitter is not None:
        max_jitter_seconds = max(int(max_jitter.total_seconds()), 0)
        if max_jitter_seconds > 0:
            jitter_seconds = _deterministic_jitter_seconds(
                f"{frequency}:{jitter_key}",
                max_jitter_seconds,
            )

    jitter = timedelta(seconds=jitter_seconds)
    if reference_includes_jitter and jitter_seconds:
        base_reference -= jitter

    candidate_base = base_reference
    while True:
        candidate_base = _advance_schedule_base(frequency, candidate_base)
        scheduled = candidate_base + jitter
        if minimum_after_utc is None or scheduled > minimum_after_utc:
            return scheduled


# Numeric ranks for plotting SSL Labs grades over time. Higher is better; trust and
# mismatch failures sit below F so a regression is visually obvious on the chart.
GRADE_RANKS: dict[str, int] = {
    "A+": 7,
    "A": 6,
    "A-": 5,
    "B": 4,
    "C": 3,
    "D": 2,
    "E": 1,
    "F": 0,
    "T": -1,
    "M": -1,
    "MIXED": -1,
}


def grade_to_rank(grade: str | None) -> int | None:
    """Map an SSL Labs grade to a numeric rank for charting, or None if unknown."""
    if grade is None:
        return None
    return GRADE_RANKS.get(grade.upper().strip())


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


def is_ssllabs_scan_active(status: SslLabsScanStatus | str | None) -> bool:
    return (status or "").strip().lower() in SSLLABS_ACTIVE_SCAN_STATUSES


def is_ssllabs_scan_terminal(status: SslLabsScanStatus | str | None) -> bool:
    return (status or "").strip().lower() in SSLLABS_TERMINAL_SCAN_STATUSES


def is_ssllabs_scan_failed(status: SslLabsScanStatus | str | None) -> bool:
    return (status or "").strip().lower() in SSLLABS_FAILED_SCAN_STATUSES


def ssllabs_scan_event_action(status: SslLabsScanStatus | str | None) -> str:
    normalized_status = (status or "").strip().lower()
    if normalized_status == "queued":
        return "scan_started"
    if normalized_status == "ready":
        return "scan_completed"
    if is_ssllabs_scan_failed(normalized_status):
        return "scan_failed"
    return "scan_updated"


def status_badge_class(status: SslLabsScanStatus | str | None, grade: str | None = None) -> str:
    """Return status pill class for scan status (used for overall status display)."""
    normalized_status = (status or "").lower()
    normalized_grade = (grade or "").upper()
    if is_ssllabs_scan_failed(normalized_status):
        return "status-pill--offline"
    if is_ssllabs_scan_active(normalized_status):
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
