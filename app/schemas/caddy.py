#!/usr/bin/env python3
#
# app/schemas/caddy.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.caddyfile import normalize_caddy_directives
from app.utils.domains import normalize_domain_list


def _normalize_domain(value: str) -> str:
    return normalize_domain_list(
        value,
        required_message="domain is required",
        length_message="domain must not exceed 253 characters",
        invalid_message="domain must be a valid DNS name",
        ip_message="domain must not be an IP address",
    )


def _normalize_upstream_url(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("upstream_url is required")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("upstream_url must not contain newlines")

    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("upstream_url must use http or https")
    if not parsed.hostname:
        raise ValueError("upstream_url must include a host")
    if parsed.query or parsed.fragment:
        raise ValueError("upstream_url must not include query or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("upstream_url must not include a path")
    return normalized


class CaddyStatusResponse(BaseModel):
    managed: bool
    onboarding_required: bool
    caddyfile_path: str
    caddyfile_marker_present: bool
    admin_api_reachable: bool
    last_synced_config_sha256: str | None = None
    error: str | None = None


class CaddyOnboardResponse(BaseModel):
    status: str
    snapshot_sha256: str | None = None
    synced: bool
    detail: str | None = None


class CaddySyncResponse(BaseModel):
    status: str
    config_sha256: str | None = None
    synced: bool
    detail: str | None = None


class SiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    upstream_url: str
    caddy_directives: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class SiteCreateRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=4096)
    caddy_directives: str = Field(min_length=1, max_length=524288)
    enabled: bool = True

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, value: str) -> str:
        return _normalize_domain(value)

    @field_validator("caddy_directives")
    @classmethod
    def _validate_caddy_directives(cls, value: str) -> str:
        normalized = normalize_caddy_directives(value)
        if normalized is None:
            raise ValueError("config is required")
        return normalized


class SiteUpdateRequest(BaseModel):
    domain: str | None = Field(default=None, min_length=1, max_length=4096)
    caddy_directives: str | None = Field(default=None, min_length=1, max_length=524288)
    enabled: bool | None = None

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_domain(value)

    @field_validator("caddy_directives")
    @classmethod
    def _validate_caddy_directives(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_caddy_directives(value)
        if normalized is None:
            raise ValueError("config is required")
        return normalized


class SiteMutationResponse(BaseModel):
    status: str
    site: SiteResponse | None = None
    sync_status: str
    synced: bool
    config_sha256: str | None = None
    sync_error: str | None = None


class SiteDeleteResponse(BaseModel):
    status: str
    sync_status: str
    config_sha256: str | None = None
    sync_error: str | None = None