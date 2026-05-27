#!/usr/bin/env python3
#
# app/schemas/ssllabs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from typing import Literal


type SslLabsScheduleFrequency = Literal["weekly", "monthly"]
type SslLabsScanStatus = Literal[
    "queued",
    "starting",
    "dns",
    "in_progress",
    "ready",
    "error",
    "failed",
    "rate_limited",
]
type SslLabsStartMode = Literal["cache", "fresh"]