#!/usr/bin/env python3
#
# app/routers/ui/auth.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

# Authentication routes (login/logout).
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import (
    get_session_user,
    initialize_user_session,
    push_flash,
    redirect_to,
    render_template,
    validate_csrf_token,
)
from app.services.auth import auth_service

from ._common import audit_commit_and_flash, logger, safe_next, validated_form

router = APIRouter()

_MAX_PASSWORD_LENGTH = 128


async def _validate_csrf_only(request: Request) -> None:
    """Validate the CSRF token for POST actions that do not otherwise use form data."""
    form = await request.form()
    validate_csrf_token(request, str(form.get("csrf_token", "")))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await get_session_user(request, session)
    if current_user is not None:
        return redirect_to("/")
    return render_template(request, "login.html", current_user=None)


@router.post("/login")
@limiter.limit("5/minute")
async def login_action(request: Request, session: AsyncSession = Depends(get_db_session)):
    form = await validated_form(request)
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    next_path = str(form.get("next", "/")) or "/"
    if len(password) > _MAX_PASSWORD_LENGTH:
        logger.warning("Rejected login attempt due to excessive password length (%d chars)", len(password))
        await audit_commit_and_flash(
            session,
            request,
            action="login_failed",
            resource_type="user",
            details={"username": username, "reason": "excessive_password_length"},
            status_code=400,
            flashes=(("danger", "Invalid credentials."),),
        )
        return redirect_to("/login")
    logger.debug("Login attempt for username=%r password_len=%d", username, len(password))
    user = await auth_service.authenticate(session, username, password)
    if user is None:
        logger.debug("Authentication failed for username=%r", username)
        await audit_commit_and_flash(
            session,
            request,
            action="login_failed",
            resource_type="user",
            details={"username": username},
            status_code=401,
            flashes=(("danger", "Invalid credentials."),),
        )
        return redirect_to("/login")
    initialize_user_session(request, user.id)
    await audit_commit_and_flash(
        session,
        request,
        action="login_success",
        resource_type="user",
        resource_id=str(user.id),
        details={"username": user.username},
        status_code=200,
        actor=user,
        flashes=(("success", f"Welcome back, {user.username}."),),
    )
    return redirect_to(safe_next(next_path))


@router.post("/logout")
async def logout_action(request: Request, session: AsyncSession = Depends(get_db_session)):
    await _validate_csrf_only(request)
    current_user = await get_session_user(request, session)
    request.session.clear()
    if current_user is not None:
        await audit_commit_and_flash(
            session,
            request,
            action="logout",
            resource_type="user",
            resource_id=str(current_user.id),
            status_code=200,
            actor=current_user,
        )
    push_flash(request, "info", "You have been signed out.")
    return redirect_to("/login")
