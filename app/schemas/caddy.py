#!/usr/bin/env python3
#
# app/schemas/caddy.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import re
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.caddyfile import normalize_caddy_directives
from app.utils.domains import split_domain_names


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]", re.ASCII)
_MAX_SITE_DIRECTIVES_BYTES = 256 * 1024
_MAX_SITE_DOMAIN_COUNT = 25


def _normalize_domain(value: str) -> str:
    normalized_domains = split_domain_names(
        value,
        required_message="domain is required",
        length_message="domain must not exceed 253 characters",
        invalid_message="domain must be a valid DNS name",
        ip_message="domain must not be an IP address",
    )
    if len(normalized_domains) > _MAX_SITE_DOMAIN_COUNT:
        raise ValueError(f"site may not contain more than {_MAX_SITE_DOMAIN_COUNT} domains")
    return ", ".join(normalized_domains)


def _normalize_site_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("site is required")
    if _CONTROL_CHARS_RE.search(normalized):
        raise ValueError("site must not contain control characters")
    return normalized


def _require_tz_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


def _validate_directives_size(value: str) -> None:
    if len(value.encode("utf-8")) > _MAX_SITE_DIRECTIVES_BYTES:
        raise ValueError("config is too large")


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
    site_name: str
    domain: str
    upstream_url: str
    caddy_directives: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_datetimes(cls, value: datetime) -> datetime:
        return _require_tz_aware(value)


class SiteCreateRequest(BaseModel):
    site_name: str = Field(min_length=1, max_length=255)
    domain: str = Field(min_length=1, max_length=4096)
    caddy_directives: str = Field(min_length=1, max_length=_MAX_SITE_DIRECTIVES_BYTES)
    enabled: bool = True

    @field_validator("site_name")
    @classmethod
    def _validate_site_name(cls, value: str) -> str:
        return _normalize_site_name(value)

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
        _validate_directives_size(normalized)
        return normalized


class SiteUpdateRequest(BaseModel):
    site_name: str | None = Field(default=None, min_length=1, max_length=255)
    domain: str | None = Field(default=None, min_length=1, max_length=4096)
    caddy_directives: str | None = Field(default=None, min_length=1, max_length=_MAX_SITE_DIRECTIVES_BYTES)
    enabled: bool | None = None

    @field_validator("site_name")
    @classmethod
    def _validate_site_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_site_name(value)

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
        _validate_directives_size(normalized)
        return normalized

    @model_validator(mode="after")
    def _validate_non_empty_update(self) -> Self:
        if (
            self.site_name is None
            and self.domain is None
            and self.caddy_directives is None
            and self.enabled is None
        ):
            raise ValueError("at least one field must be provided")
        return self


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