#!/usr/bin/env python3
#
# app/schemas/ssllabs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from typing import Literal


SSLLABS_SCHEDULE_FREQUENCIES = ("weekly",)

SSLLABS_SCAN_STATUSES = (
    "queued",
    "starting",
    "dns",
    "in_progress",
    "ready",
    "error",
    "failed",
    "rate_limited",
)
SSLLABS_ACTIVE_SCAN_STATUSES = ("queued", "starting", "dns", "in_progress", "rate_limited")
# SQLite partial unique indexes cannot include the stale-time predicate used by
# the repository. Keep retryable rate-limit rows out of the database exclusivity
# set so stale retry rows do not permanently block a new scan.
SSLLABS_EXCLUSIVE_ACTIVE_SCAN_STATUSES = ("queued", "starting", "dns", "in_progress")
SSLLABS_TERMINAL_SCAN_STATUSES = ("ready", "error", "failed")
SSLLABS_FAILED_SCAN_STATUSES = ("error", "failed")

type SslLabsScheduleFrequency = Literal[*SSLLABS_SCHEDULE_FREQUENCIES]
type SslLabsScanStatus = Literal[*SSLLABS_SCAN_STATUSES]
type SslLabsStartMode = Literal["cache", "fresh"]
