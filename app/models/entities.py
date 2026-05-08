#!/usr/bin/env python3
#
# app/models/entities.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import enum
import hashlib
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.models.base import Base, TimestampMixin


class DeploymentStatus(enum.StrEnum):
    """Deployment state machine states."""

    PENDING = "pending"
    VALIDATING = "validating"
    VALID = "valid"
    INVALID = "invalid"
    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    FAILED = "failed"
    ROLLBACK_PENDING = "rollback_pending"
    ROLLED_BACK = "rolled_back"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(20), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="user", cascade="all, delete-orphan")
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="user")


class CaddyConfigServerLink(Base):
    __tablename__ = "caddy_config_servers"

    config_id: Mapped[int] = mapped_column(ForeignKey("caddy_configs.id", ondelete="CASCADE"), primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("caddy_servers.id", ondelete="CASCADE"), primary_key=True)


class CaddyServer(TimestampMixin, Base):
    __tablename__ = "caddy_servers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    api_url: Mapped[str] = mapped_column(String(255))
    api_port: Mapped[int] = mapped_column(Integer, default=2019)
    admin_api_path: Mapped[str] = mapped_column(String(120), default="/config/")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    tags: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JSON), default=list)
    last_pinged: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    active_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("caddy_configs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    configs: Mapped[list[CaddyConfig]] = relationship(
        secondary="caddy_config_servers",
        back_populates="servers",
        lazy="selectin",
    )

    @validates("api_url")
    def _validate_api_url(self, _key: str, value: str) -> str:
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("api_url must use http or https")
        if not parsed.hostname:
            raise ValueError("api_url must include a hostname")
        if parsed.port is not None:
            raise ValueError("api_url must not include a port; use api_port instead")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("api_url must not include a path, query, or fragment")
        return normalized.rstrip("/")

    @validates("admin_api_path")
    def _validate_admin_api_path(self, _key: str, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("/"):
            raise ValueError("admin_api_path must start with '/'")
        parsed = urlsplit(normalized)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("admin_api_path must be a relative path without query or fragment")
        return normalized


class CaddyConfig(TimestampMixin, Base):
    __tablename__ = "caddy_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    format: Mapped[str] = mapped_column(String(20), default="json")
    json_config: Mapped[dict[str, Any]] = mapped_column(MutableDict.as_mutable(JSON))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", MutableDict.as_mutable(JSON), default=dict)
    history_entries: Mapped[list[dict[str, Any]]] = mapped_column("history", MutableList.as_mutable(JSON), default=list)

    servers: Mapped[list[CaddyServer]] = relationship(
        secondary="caddy_config_servers",
        back_populates="configs",
        lazy="selectin",
    )


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    key_prefix: Mapped[str] = mapped_column(String(20), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    permissions: Mapped[dict[str, Any]] = mapped_column(MutableDict.as_mutable(JSON), default=dict)
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    user: Mapped[User] = relationship(back_populates="api_keys")


class Domain(TimestampMixin, Base):
    """Domain entry representing a site/hostname managed by Caddy."""

    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    server_id: Mapped[int | None] = mapped_column(ForeignKey("caddy_servers.id", ondelete="SET NULL"), nullable=True, index=True)
    upstream: Mapped[str | None] = mapped_column(String(255), nullable=True)
    caddy_directives: Mapped[str | None] = mapped_column(Text, nullable=True)
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ssl_provider: Mapped[str] = mapped_column(String(50), default="letsencrypt")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    server: Mapped[CaddyServer | None] = relationship("CaddyServer", lazy="selectin")

    @validates("name")
    def _validate_name(self, _key: str, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("domain name cannot be empty")
        if len(normalized) > 253:
            raise ValueError("domain name exceeds the DNS limit of 253 characters")
        return normalized


class AuditLog(TimestampMixin, Base):
    __tablename__ = "audit_logs"

    __table_args__ = (
        Index("ix_audit_logs_timestamp", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(80), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column("details", MutableDict.as_mutable(JSON), default=dict)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    user: Mapped[User | None] = relationship(back_populates="audit_logs")


# =============================================================================
# NEW ARCHITECTURE: Config Template → Site → Deployment
# =============================================================================


class ConfigTemplate(TimestampMixin, Base):
    """Reusable Caddy configuration template.

    Templates are NOT deployed directly. They are referenced by Sites,
    which are then deployed to Servers via the Deployment entity.
    """

    __tablename__ = "config_templates"

    __table_args__ = (
        UniqueConstraint("checksum", name="uq_config_templates_checksum"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    caddyfile: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    variables: Mapped[dict[str, Any]] = mapped_column(MutableDict.as_mutable(JSON), default=dict)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version_id}

    sites: Mapped[list[Site]] = relationship(back_populates="config_template", lazy="selectin")
    revisions: Mapped[list[ConfigRevision]] = relationship(
        back_populates="template",
        lazy="selectin",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @staticmethod
    def compute_checksum(caddyfile: str) -> str:
        """Compute SHA-256 checksum of Caddyfile content."""
        return hashlib.sha256(caddyfile.encode("utf-8")).hexdigest()


class Site(TimestampMixin, Base):
    """A site/virtual host representing a domain with a configuration template.

    CRITICAL: The domain field has a UNIQUE constraint enforced at DB level.
    This prevents duplicate domain assignments and makes the UI simpler.

    A Site is the business unit:
    - One domain
    - One configuration template
    - Zero or more deployments to servers
    """

    __tablename__ = "sites"

    __table_args__ = (
        UniqueConstraint("domain", name="uq_sites_domain"),
        Index("ix_sites_enabled", "enabled"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    config_template_id: Mapped[int] = mapped_column(
        ForeignKey("config_templates.id", ondelete="RESTRICT"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    variables: Mapped[dict[str, Any]] = mapped_column(MutableDict.as_mutable(JSON), default=dict)
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ssl_provider: Mapped[str] = mapped_column(String(50), default="letsencrypt")

    config_template: Mapped[ConfigTemplate] = relationship(back_populates="sites", lazy="selectin")
    deployments: Mapped[list[Deployment]] = relationship(
        back_populates="site", lazy="selectin", cascade="all, delete-orphan"
    )

    @validates("domain")
    def _validate_domain(self, _key: str, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("site domain cannot be empty")
        if len(normalized) > 253:
            raise ValueError("site domain exceeds the DNS limit of 253 characters")
        return normalized


class Deployment(TimestampMixin, Base):
    """Deployment record linking a Site to a Server.

    This is the key entity for the new architecture:
    - Tracks deployment state machine
    - Stores rendered configuration (immutable after deployment)
    - Enables rollback via deployment history

    Flow: Site → Render → Validate → Deploy → Server
    """

    __tablename__ = "deployments"

    __table_args__ = (
        Index("ix_deployments_site_server", "site_id", "server_id"),
        Index("ix_deployments_status", "status"),
        Index("ix_deployments_deployed_at", "deployed_at"),
        Index(
            "uq_active_deployment_per_site_server",
            "site_id",
            "server_id",
            unique=True,
            sqlite_where=text("status = 'DEPLOYED'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), index=True
    )
    server_id: Mapped[int] = mapped_column(
        ForeignKey("caddy_servers.id", ondelete="CASCADE"), index=True
    )
    rendered_config: Mapped[str] = mapped_column(Text)
    rendered_checksum: Mapped[str] = mapped_column(String(64), index=True)
    deployed_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[DeploymentStatus] = mapped_column(
        Enum(DeploymentStatus), default=DeploymentStatus.PENDING
    )
    validation_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployment_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deployed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rollback_deployment_id: Mapped[int | None] = mapped_column(
        ForeignKey("deployments.id", ondelete="SET NULL"), nullable=True
    )

    __mapper_args__ = {"version_id_col": version}

    site: Mapped[Site] = relationship(back_populates="deployments", lazy="selectin")
    server: Mapped[CaddyServer] = relationship(lazy="selectin")
    rollback_source: Mapped[Deployment | None] = relationship(
        "Deployment", remote_side=[id], lazy="selectin"
    )

    def _require_status(self, expected: DeploymentStatus, target: DeploymentStatus) -> None:
        if self.status != expected:
            raise RuntimeError(
                f"Cannot transition to {target.value} from {self.status.value}"
            )

    def mark_validated(self, output: str | None = None) -> None:
        """Transition to VALID state."""
        self._require_status(DeploymentStatus.VALIDATING, DeploymentStatus.VALID)
        self.status = DeploymentStatus.VALID
        self.validation_output = output

    def mark_invalid(self, error: str) -> None:
        """Transition to INVALID state."""
        self._require_status(DeploymentStatus.VALIDATING, DeploymentStatus.INVALID)
        self.status = DeploymentStatus.INVALID
        self.validation_output = error

    def mark_deploying(self) -> None:
        """Transition to DEPLOYING state."""
        if self.status not in {DeploymentStatus.PENDING, DeploymentStatus.VALID}:
            raise RuntimeError(
                f"Cannot transition to {DeploymentStatus.DEPLOYING.value} from {self.status.value}"
            )
        self.status = DeploymentStatus.DEPLOYING

    def mark_deployed(self, deployed_by: str | None = None) -> None:
        """Transition to DEPLOYED state."""
        self._require_status(DeploymentStatus.DEPLOYING, DeploymentStatus.DEPLOYED)
        self.status = DeploymentStatus.DEPLOYED
        self.deployed_checksum = self.rendered_checksum
        self.deployed_at = datetime.now(UTC)
        self.deployed_by = deployed_by

    def mark_failed(self, error: str) -> None:
        """Transition to FAILED state."""
        if self.status not in {DeploymentStatus.DEPLOYING, DeploymentStatus.ROLLBACK_PENDING}:
            raise RuntimeError(
                f"Cannot transition to {DeploymentStatus.FAILED.value} from {self.status.value}"
            )
        self.status = DeploymentStatus.FAILED
        self.deployment_error = error


class ConfigRevision(TimestampMixin, Base):
    """Immutable revision record for ConfigTemplate changes.

    Enables:
    - Audit trail of configuration changes
    - Rollback to previous versions
    - Diff comparison between versions
    """

    __tablename__ = "config_revisions"

    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_config_revisions_template_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("config_templates.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    caddyfile: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64))
    variables: Mapped[dict[str, Any]] = mapped_column(MutableDict.as_mutable(JSON), default=dict)
    change_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(80), nullable=True)

    template: Mapped[ConfigTemplate] = relationship(back_populates="revisions")