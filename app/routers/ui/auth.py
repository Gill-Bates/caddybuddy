#!/usr/bin/env python3
#
# app/routers/ui/auth.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.dependencies.web import (
    get_session_user,
    initialize_user_session,
    push_flash,
    redirect_to,
    render_template,
    validate_csrf_token,
)
from app.repositories.users import user_repository
from app.services.auth import PASSWORD_MIN_LENGTH, PASSWORD_POLICY_MESSAGE, WeakPasswordError, auth_service

from ._common import commit_and_flash, logger, safe_next, validated_form

router = APIRouter()

_MAX_USERNAME_LENGTH = 50
_MAX_PASSWORD_LENGTH = 4096


def _render_login_failure(request: Request, *, next_path: str, status_code: int = 403) -> HTMLResponse:
    push_flash(request, "danger", "Invalid credentials.")
    return render_template(
        request,
        "login.html",
        current_user=None,
        context={
            "safe_next_url": safe_next(next_path),
            "auth_error": True,
            "password_policy_min_length": PASSWORD_MIN_LENGTH,
            "password_policy_message": PASSWORD_POLICY_MESSAGE,
        },
        status_code=status_code,
    )


async def _validate_csrf_only(request: Request) -> None:
    """Validate the CSRF token for POST actions that do not otherwise use form data."""
    form = await request.form()
    validate_csrf_token(request, str(form.get("csrf_token", "")))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    current_user = await get_session_user(request, session)
    if current_user is not None:
        return redirect_to("/")
    setup_mode = not await user_repository.exists_any(session)
    safe_next_url = safe_next(str(request.query_params.get("next", "/")) or "/")
    return render_template(
        request,
        "login.html",
        current_user=None,
        context={
            "safe_next_url": safe_next_url,
            "setup_mode": setup_mode,
            "password_policy_min_length": PASSWORD_MIN_LENGTH,
            "password_policy_message": PASSWORD_POLICY_MESSAGE,
        },
    )


@router.post("/setup")
@limiter.limit("10/minute")
async def setup_action(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    if await user_repository.exists_any(session):
        return redirect_to("/login")

    form = await validated_form(request)
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm_password", ""))

    def _error(message: str) -> HTMLResponse:
        push_flash(request, "danger", message)
        return render_template(
            request,
            "login.html",
            current_user=None,
            context={
                "safe_next_url": "/",
                "setup_mode": True,
                "setup_error": message,
                "password_policy_min_length": PASSWORD_MIN_LENGTH,
                "password_policy_message": PASSWORD_POLICY_MESSAGE,
            },
            status_code=422,
        )

    if len(password) > _MAX_PASSWORD_LENGTH:
        return _error("Password is too long.")
    if password != confirm:
        return _error("Passwords do not match.")

    try:
        settings = get_settings()
        user = await auth_service.ensure_default_admin(
            session,
            username=settings.default_admin_username,
            password=password,
            email=settings.default_admin_email,
        )
    except WeakPasswordError as exc:
        return _error(str(exc))

    if user is None:
        return redirect_to("/login")
    await session.commit()
    initialize_user_session(request, user.id, user.password_hash)
    logger.info("First admin account created via setup UI: username=%r", user.username)
    push_flash(request, "success", f"Welcome, {user.username}. Your admin account has been created.")
    return redirect_to("/onboarding")


@router.post("/login")
@limiter.limit("5/minute;20/hour")
async def login_action(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    form = await validated_form(request)
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    next_path = str(form.get("next", "/")) or "/"
    if len(username) > _MAX_USERNAME_LENGTH:
        logger.warning(
            "Rejected login attempt due to excessive username length (%d chars) status_code=403",
            len(username),
        )
        return _render_login_failure(request, next_path=next_path)
    if len(password) > _MAX_PASSWORD_LENGTH:
        logger.warning(
            "Rejected login attempt due to excessive password length (%d chars) status_code=403",
            len(password),
        )
        return _render_login_failure(request, next_path=next_path)
    logger.debug("Login attempt for username=%r", username)
    user = await auth_service.authenticate(session, username, password)
    if user is None:
        logger.warning("Authentication failed for username=%r status_code=403", username)
        return _render_login_failure(request, next_path=next_path)
    initialize_user_session(request, user.id, user.password_hash)
    await commit_and_flash(
        session,
        request,
        flashes=(("success", f"Welcome back, {user.username}."),),
    )
    return redirect_to(safe_next(next_path))


@router.post("/logout")
async def logout_action(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    await _validate_csrf_only(request)
    request.session.clear()
    return redirect_to("/login")
