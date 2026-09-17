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
from fastapi.responses import Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from app.dependencies.web import (
    get_session_user,
    push_flash,
    redirect_to,
    validate_csrf_token,
)
from app.models.entities import User
from app.services.caddy_onboarding import get_onboarding_state

logger = logging.getLogger(__name__)

_UNSAFE_NEXT_PATH_RE = re.compile(r"[\x00-\x1f\x7f\\]")
_MAX_FORM_BODY_BYTES = 2 * 1024 * 1024
_CHECKBOX_TRUE_VALUES = frozenset({"1", "true", "on", "yes"})


async def require_onboarding_completed(session: AsyncSession) -> Response | None:
    """Return a redirect to /onboarding when the wizard has not been completed, else None."""
    state = await get_onboarding_state(session)
    if state.status != "completed":
        return redirect_to("/onboarding")
    return None


async def require_user(request: Request, session: AsyncSession) -> User | None:
    """Return the current user, or None if not authenticated.

    UI routes use this instead of ``app.dependencies.web.require_api_user`` so an
    anonymous visitor gets a redirect or flash rather than a 401 body.
    """
    return await get_session_user(request, session)


async def require_admin(request: Request, session: AsyncSession) -> User | None:
    """Return the current user if admin, else None (with flash)."""
    current_user = await require_user(request, session)
    if current_user is None:
        return None
    if current_user.role != "admin":
        push_flash(request, "danger", "Administrator access is required.")
        return None
    return current_user


async def validated_csrf_form(request: Request) -> FormData:
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
        or next_path.startswith(("//", "/\\"))
        or decoded_path is None
        or not decoded_path.startswith("/")
        or decoded_path.startswith(("//", "/\\"))
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


def parse_checkbox(value: object) -> bool:
    """Return True when a form value represents a checked checkbox.

    Browsers submit ``on`` for a checked box, but the same forms are also driven
    by fetch/JSON clients that send ``true``/``1``/``yes``; an unchecked box is
    omitted entirely and therefore arrives here as ``None``.
    """
    return str(value or "").strip().lower() in _CHECKBOX_TRUE_VALUES


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
