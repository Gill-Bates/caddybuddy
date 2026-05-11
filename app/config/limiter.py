#!/usr/bin/env python3
#
# app/config/limiter.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config.settings import get_settings

settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,
    enabled=not settings.allow_insecure_defaults
)