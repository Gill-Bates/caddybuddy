#!/usr/bin/env python3
#
# app/schemas/system.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str


class BuildInfoResponse(BaseModel):
    version: str
    commit: str
    build_date: str


class CaddyStatusResponse(BaseModel):
    """Caddy service status for dashboard badge auto-refresh."""
    running: bool
    status: str
    uptime: str
    version: str


class DashboardMetricsResponse(BaseModel):
    domain_count: int
    enabled_domain_count: int
    valid_certificate_count: int | None
    expired_certificate_count: int | None
    expiring_soon_certificate_count: int | None
    caddy_service_status: str
    caddy_service_uptime: str
    caddy_version: str