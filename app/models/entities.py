#!/usr/bin/env python3
#
# app/models/entities.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
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
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_pinged: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    active_config_id: Mapped[int | None] = mapped_column(ForeignKey("caddy_configs.id", ondelete="SET NULL"), nullable=True)

    configs: Mapped[list[CaddyConfig]] = relationship(
        secondary="caddy_config_servers",
        back_populates="servers",
        lazy="selectin",
    )


class CaddyConfig(TimestampMixin, Base):
    __tablename__ = "caddy_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    format: Mapped[str] = mapped_column(String(20), default="json")
    json_config: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    history_entries: Mapped[list[dict]] = mapped_column("history", JSON, default=list)

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
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    user: Mapped[User] = relationship(back_populates="api_keys")


class AuditLog(TimestampMixin, Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(80), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    details_json: Mapped[dict] = mapped_column("details", JSON, default=dict)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    user: Mapped[User | None] = relationship(back_populates="audit_logs")


Index("ix_audit_logs_timestamp", AuditLog.timestamp)