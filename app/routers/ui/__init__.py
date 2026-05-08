#!/usr/bin/env python3
#
# app/routers/ui/__init__.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Aggregated UI router."""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    api_keys,
    audit_logs,
    auth,
    dashboard,
    deployments,
    profile,
    servers,
    sites,
    templates,
    users,
)

router = APIRouter()

router.include_router(auth.router)
router.include_router(dashboard.router)
router.include_router(servers.router)
router.include_router(sites.router)
router.include_router(templates.router)
router.include_router(deployments.router)
router.include_router(api_keys.router)
router.include_router(users.router)
router.include_router(audit_logs.router)
router.include_router(profile.router)
