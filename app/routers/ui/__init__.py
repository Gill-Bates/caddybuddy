#!/usr/bin/env python3
#
# app/routers/ui/__init__.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Aggregated UI router - simplified architecture."""

from __future__ import annotations

from fastapi import APIRouter

_router: APIRouter | None = None


def _build_router() -> APIRouter:
    from . import auth, caddyfile, dashboard, onboarding, settings, sites, ssllabs

    ui_router = APIRouter()
    ui_router.include_router(auth.router)
    ui_router.include_router(onboarding.router)
    ui_router.include_router(dashboard.router)
    ui_router.include_router(caddyfile.router)
    ui_router.include_router(sites.router)
    ui_router.include_router(ssllabs.router)
    ui_router.include_router(settings.router)
    return ui_router


def __getattr__(name: str):
    if name != "router":
        raise AttributeError(name)

    global _router
    if _router is None:
        _router = _build_router()
    return _router
