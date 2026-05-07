#!/usr/bin/env python3
#
# app/routers/ui/__init__.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

# Aggregated UI router.
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from fastapi import APIRouter

from . import api_keys, audit_logs, auth, configs, dashboard, domains, profile, servers, users

router = APIRouter()

# Include all sub-routers
router.include_router(auth.router)
router.include_router(dashboard.router)
router.include_router(servers.router)
router.include_router(configs.router)
router.include_router(profile.router)
router.include_router(users.router)
router.include_router(api_keys.router)
router.include_router(domains.router)
router.include_router(audit_logs.router)
