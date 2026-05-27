#!/usr/bin/env python3
#
# app/config/limiter.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    enabled=True,  # Default enabled; updated from DB at startup
)


def update_rate_limit_enabled(enabled: bool) -> None:
    """Update the limiter's enabled state at runtime."""
    limiter.enabled = enabled