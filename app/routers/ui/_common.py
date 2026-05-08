#!/usr/bin/env python3
#
# app/routers/ui/_common.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from app.dependencies.web import get_session_user, push_flash, validate_csrf_token
from app.models.entities import ApiKey, AuditLog, CaddyConfig, CaddyServer, User
from app.repositories.api_keys import api_key_repository
from app.repositories.audit_logs import audit_log_repository
from app.repositories.servers import server_repository
from app.services.audit import audit_service

logger = logging.getLogger(__name__)


async def require_user(request: Request, session: AsyncSession) -> User | None:
    """Return the current user or None (with flash) if not authenticated."""
    current_user = await get_session_user(request, session)
    if current_user is None:
        push_flash(request, "warning", "Please sign in to continue.")
        return None
    return current_user


async def require_admin(request: Request, session: AsyncSession) -> User | None:
    """Return the current user if admin, else None (with flash)."""
    current_user = await require_user(request, session)
    if current_user is None:
        return None
    if current_user.role != "admin":
        push_flash(request, "danger", "Administrator access is required.")
        return None
    return current_user


async def validated_form(request: Request) -> FormData:
    """Parse form data and validate CSRF token."""
    form = await request.form()
    validate_csrf_token(request, str(form.get("csrf_token", "")))
    return form


def safe_next(next_path: str | None) -> str:
    """Sanitize the 'next' redirect path to prevent open redirects."""
    if (
        not next_path
        or not next_path.startswith("/")
        or next_path.startswith("//")
        or next_path.startswith("/\\")
    ):
        return "/"
    return next_path


def parse_int(value: object, *, default: int | None = None) -> int | None:
    """Parse an integer from a form value, returning default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def audit_commit_and_flash(
    session: AsyncSession,
    request: Request,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict | None = None,
    status_code: int = 200,
    actor: User | None = None,
    flashes: Sequence[tuple[str, str]] = (),
) -> None:
    """Log an audit action, commit, and push flash messages."""
    await audit_service.log_action(
        session,
        action=action,
        resource_type=resource_type,
        request=request,
        resource_id=resource_id,
        details=details or {},
        status_code=status_code,
        actor=actor,
    )
    try:
        await session.commit()
    except SQLAlchemyError:
        logger.exception(
            "Failed to commit audit action '%s' for resource type '%s'",
            action,
            resource_type,
        )
        try:
            await session.rollback()
        except SQLAlchemyError:
            logger.exception(
                "Failed to roll back audit action '%s' for resource type '%s'",
                action,
                resource_type,
            )
        raise
    for category, message in flashes:
        push_flash(request, category, message)


async def load_dashboard_context(session: AsyncSession) -> dict[str, object]:
    """Load dashboard statistics and recent data."""
    counts = (
        await session.execute(
            select(
                select(func.count()).select_from(CaddyServer).scalar_subquery().label("server_count"),
                select(func.count()).select_from(CaddyConfig).scalar_subquery().label("config_count"),
                select(func.count()).select_from(ApiKey).scalar_subquery().label("api_key_count"),
                select(func.count()).select_from(AuditLog).scalar_subquery().label("audit_count"),
            )
        )
    ).one()
    return {
        "server_count": int(counts.server_count),
        "config_count": int(counts.config_count),
        "api_key_count": int(counts.api_key_count),
        "audit_count": int(counts.audit_count),
        "servers": await server_repository.list_all(session, limit=5),
        "recent_logs": await audit_log_repository.list_recent(session, limit=8),
    }


def config_history_entry(action: str, actor: str, note: str) -> dict[str, str]:
    """Create a config history entry dict."""
    return {
        "action": action,
        "actor": actor,
        "note": note,
        "timestamp": datetime.now(UTC).isoformat(),
    }


async def load_api_keys(
    session: AsyncSession,
    current_user: User,
    *,
    limit: int = 100,
) -> list[ApiKey]:
    """Return all API keys for admins, else only keys owned by ``current_user``."""
    if current_user.role == "admin":
        return await api_key_repository.list_all(session, limit=limit)
    return await api_key_repository.list_for_user(session, current_user.id, limit=limit)
