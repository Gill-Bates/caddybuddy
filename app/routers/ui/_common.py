#!/usr/bin/env python3
#
# app/routers/ui/_common.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from urllib.parse import unquote

from fastapi import HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from app.dependencies.web import get_session_user, push_flash, validate_csrf_token
from app.models.entities import User

logger = logging.getLogger(__name__)

_UNSAFE_NEXT_PATH_RE = re.compile(r"[\x00-\x1f\x7f\\]")
_MAX_FORM_BODY_BYTES = 2 * 1024 * 1024


async def require_user(request: Request, session: AsyncSession) -> User | None:
    """Return the current user or None if not authenticated."""
    current_user = await get_session_user(request, session)
    if current_user is None:
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
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            parsed_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid form body length.") from exc
        if parsed_length > _MAX_FORM_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Form body too large.")
    form = await request.form()
    validate_csrf_token(request, str(form.get("csrf_token", "")))
    return form


def safe_next(next_path: str | None) -> str:
    """Sanitize the 'next' redirect path to prevent open redirects."""
    decoded_path = unquote(next_path) if next_path else None
    if (
        not next_path
        or not next_path.startswith("/")
        or next_path.startswith("//")
        or next_path.startswith("/\\")
        or decoded_path is None
        or not decoded_path.startswith("/")
        or decoded_path.startswith("//")
        or decoded_path.startswith("/\\")
        or _UNSAFE_NEXT_PATH_RE.search(next_path) is not None
        or _UNSAFE_NEXT_PATH_RE.search(decoded_path) is not None
    ):
        return "/"
    return next_path


def parse_int(value: object, *, default: int | None = None) -> int | None:
    """Parse an integer from a form value, returning default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def commit_and_flash(
    session: AsyncSession,
    request: Request,
    *,
    flashes: Sequence[tuple[str, str]] = (),
) -> None:
    """Commit the current transaction and push flash messages."""
    try:
        await session.commit()
    except SQLAlchemyError:
        logger.exception("Failed to commit current transaction")
        if getattr(session, "is_active", False):
            try:
                await session.rollback()
            except SQLAlchemyError:
                logger.exception("Failed to roll back current transaction")
        raise
    for category, message in flashes:
        push_flash(request, category, message)
