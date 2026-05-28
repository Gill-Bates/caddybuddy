#!/usr/bin/env python3
#
# app/models/entities.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import datetime
import re
from urllib.parse import urlsplit

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.models.base import Base, TimestampMixin, UTCDateTime, utc_now
from app.utils.caddyfile import normalize_caddy_directives
from app.utils.domains import normalize_domain_list
from app.utils.ssllabs import validate_ssllabs_host


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
_SYNC_EVENT_STATUSES = (
    "synced",
    "sync_failed",
    "validation_failed",
    "no_change",
    "onboarding_failed",
)
_SSLLABS_SCAN_STATUSES = (
    "queued",
    "starting",
    "dns",
    "in_progress",
    "ready",
    "error",
    "failed",
    "rate_limited",
)
_SSLLABS_SCHEDULE_FREQUENCIES = ("weekly", "monthly")
_USER_ROLES = ("user", "admin")


def _sql_string_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _normalize_domain_name(value: str) -> str:
    return normalize_domain_list(value)


def _normalize_site_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("site_name cannot be empty")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("site_name must not contain newlines")
    return normalized


def _normalize_upstream_url(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("upstream_url cannot be empty")
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


def _normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValueError("invalid sha256")
    return normalized


def _normalize_sync_event_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in _SYNC_EVENT_STATUSES:
        raise ValueError("invalid caddy sync event status")
    return normalized


def _normalize_ssllabs_scan_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in _SSLLABS_SCAN_STATUSES:
        raise ValueError("invalid ssllabs scan status")
    return normalized


def _normalize_ssllabs_schedule_frequency(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized not in _SSLLABS_SCHEDULE_FREQUENCIES:
        raise ValueError("invalid ssllabs schedule frequency")
    return normalized


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            f"role IN ({_sql_string_list(_USER_ROLES)})",
            name="ck_users_role",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(50, collation="NOCASE"),
        nullable=False,
        unique=True,
        index=True,
    )
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @validates("role")
    def _validate_role(self, _key: str, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _USER_ROLES:
            raise ValueError("invalid user role")
        return normalized

    @validates("email")
    def _validate_email(self, _key: str, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None


class CaddyBuddyState(Base):
    __tablename__ = "caddybuddy_state"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class CaddyfileSnapshot(Base):
    __tablename__ = "caddyfile_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    imported_by: Mapped[str] = mapped_column(String(50), nullable=False, default="onboarding")

    @validates("sha256")
    def _validate_sha256(self, _key: str, value: str) -> str:
        return _normalize_sha256(value)


class CaddyConfigVersion(Base):
    __tablename__ = "caddy_config_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rendered_config: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    @validates("sha256")
    def _validate_sha256(self, _key: str, value: str) -> str:
        return _normalize_sha256(value)


class CaddySyncEvent(Base):
    __tablename__ = "caddy_sync_events"

    __table_args__ = (
        CheckConstraint(
            "status IN ('synced', 'sync_failed', 'validation_failed', 'no_change', 'onboarding_failed')",
            name="ck_caddy_sync_events_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    config_sha256: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("caddy_config_versions.sha256"),
        nullable=True,
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    @validates("status")
    def _validate_status(self, _key: str, value: str) -> str:
        return _normalize_sync_event_status(value)

    @validates("config_sha256")
    def _validate_config_sha256(self, _key: str, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_sha256(value)


class Site(TimestampMixin, Base):
    """A single managed Caddy site."""

    __tablename__ = "caddy_sites"

    __table_args__ = (
        UniqueConstraint("domain", name="uq_caddy_sites_domain"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    site_name: Mapped[str] = mapped_column(String(255, collation="NOCASE"), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    upstream_url: Mapped[str] = mapped_column(Text, nullable=False)
    caddy_directives: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    @validates("site_name")
    def _validate_site_name(self, _key: str, value: str) -> str:
        return _normalize_site_name(value)

    @validates("domain")
    def _validate_domain(self, _key: str, value: str) -> str:
        return _normalize_domain_name(value)

    @validates("upstream_url")
    def _validate_upstream_url(self, _key: str, value: str) -> str:
        return _normalize_upstream_url(value)

    @validates("caddy_directives")
    def _validate_caddy_directives(self, _key: str, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_caddy_directives(value)
        return normalized or None


class SslLabsTarget(TimestampMixin, Base):
    __tablename__ = "ssllabs_targets"

    __table_args__ = (
        CheckConstraint(
            "schedule_frequency IS NULL OR schedule_frequency IN ('weekly', 'monthly')",
            name="ck_ssllabs_targets_schedule_frequency",
        ),
        UniqueConstraint("site_id", "host", name="uq_ssllabs_targets_site_host"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("caddy_sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    host: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    schedule_frequency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    next_scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    last_scan_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_scan_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    @validates("host")
    def _validate_host(self, _key: str, value: str) -> str:
        return validate_ssllabs_host(value)

    @validates("schedule_frequency")
    def _validate_schedule_frequency(self, _key: str, value: str | None) -> str | None:
        return _normalize_ssllabs_schedule_frequency(value)


class SslLabsScan(Base):
    __tablename__ = "ssllabs_scans"

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'starting', 'dns', 'in_progress', 'ready', 'error', 'failed', 'rate_limited')",
            name="ck_ssllabs_scans_status",
        ),
        CheckConstraint(
            "endpoint_count >= 0",
            name="ck_ssllabs_scans_endpoint_count_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(
        ForeignKey("ssllabs_targets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    site_id: Mapped[int] = mapped_column(
        ForeignKey("caddy_sites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    host: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True, default="queued")
    grade: Mapped[str | None] = mapped_column(String(8), nullable=True)
    endpoint_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    next_poll_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)

    @validates("host")
    def _validate_host(self, _key: str, value: str) -> str:
        return validate_ssllabs_host(value)

    @validates("status")
    def _validate_status(self, _key: str, value: str) -> str:
        return _normalize_ssllabs_scan_status(value)

    @validates("endpoint_count")
    def _validate_endpoint_count(self, _key: str, value: int) -> int:
        if value < 0:
            raise ValueError("endpoint_count must be non-negative")
        return value


_APP_SETTING_KEYS = (
    "caddy_api_url",
    "caddyfile_path",
    "rate_limit_enabled",
    "ssllabs_email",
)


class AppSetting(Base, TimestampMixin):
    """Key-value storage for application configuration."""

    __tablename__ = "app_settings"
    __table_args__ = (
        CheckConstraint(
            f"key IN ({_sql_string_list(_APP_SETTING_KEYS)})",
            name="ck_app_settings_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    @validates("key")
    def _validate_key(self, _key: str, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _APP_SETTING_KEYS:
            raise ValueError(f"Invalid setting key: {value!r}")
        return normalized