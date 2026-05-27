#!/usr/bin/env python3
#
# app/config/limiter.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

_limiter_enabled = os.environ.get("CB_RATE_LIMIT_ENABLED", "true").lower() not in ("false", "0", "no")

limiter = Limiter(
    key_func=get_remote_address,
    enabled=_limiter_enabled,
)