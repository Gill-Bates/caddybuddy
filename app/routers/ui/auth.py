#!/usr/bin/env python3
#
# app/routers/ui/auth.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import hmac
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.dependencies.web import (
    get_session_user,
    initialize_pending_otp_session,
    initialize_user_session,
    push_flash,
    redirect_to,
    render_template,
    user_session_fingerprint,
    validate_csrf_token,
)
from app.repositories.users import user_repository
from app.services.auth import (
    PASSWORD_MIN_LENGTH,
    PASSWORD_POLICY_MESSAGE,
    WeakPasswordError,
    auth_service,
)
from app.services.passkeys import passkey_service
from app.utils.hidden_captcha import (
    HONEYPOT_FIELD_NAME,
    CaptchaOutcome,
    issue_captcha_token,
    verify_captcha_token,
)
from app.utils.security_logging import log_authentication_failure

from ._common import commit_and_flash, logger, safe_next, validated_csrf_form

router = APIRouter()

_MAX_USERNAME_LENGTH = 50
_MAX_PASSWORD_LENGTH = 4096
_MAX_OTP_CODE_LENGTH = 64
_OTP_CHALLENGE_TTL_SECONDS = 180
_CAPTCHA_REJECTED_MESSAGE = "We couldn't verify your submission. Please reload the page and try again."


def _captcha_context() -> dict[str, str]:
    """Return a fresh anti-bot challenge for an authentication form."""
    return {
        "captcha_token": issue_captcha_token(get_settings().secret_key.get_secret_value()),
        "honeypot_field": HONEYPOT_FIELD_NAME,
    }


async def _render_login_failure(
    request: Request,
    session: AsyncSession,
    *,
    next_path: str,
    status_code: int = 403,
    message: str = "Invalid credentials.",
) -> HTMLResponse:
    push_flash(request, "danger", message)
    return render_template(
        request,
        "login.html",
        current_user=None,
        context={
            "safe_next_url": safe_next(next_path),
            "auth_error": True,
            "auth_error_message": message,
            "passkey_login_available": await passkey_service.any_registered(session),
            "password_policy_min_length": PASSWORD_MIN_LENGTH,
            "password_policy_message": PASSWORD_POLICY_MESSAGE,
            **_captcha_context(),
        },
        status_code=status_code,
    )


def _pending_otp_challenge(request: Request) -> tuple[int, str, str] | None:
    """Return the current session-bound OTP challenge, clearing invalid state."""
    try:
        user_id = int(request.session.get("otp_pending_user_id"))
        created_at = float(request.session.get("otp_pending_created_at"))
        fingerprint = str(request.session.get("otp_pending_fingerprint", ""))
    except (TypeError, ValueError):
        return None
    if (
        user_id <= 0
        or created_at <= 0
        or not fingerprint
        or time.time() - created_at > _OTP_CHALLENGE_TTL_SECONDS
    ):
        request.session.clear()
        return None
    return user_id, safe_next(str(request.session.get("otp_pending_next", "/"))), fingerprint


def _render_otp_challenge(
    request: Request,
    *,
    next_path: str,
    status_code: int = 200,
    error_message: str | None = None,
) -> HTMLResponse:
    return render_template(
        request,
        "otp_login.html",
        current_user=None,
        context={
            "safe_next_url": safe_next(next_path),
            "otp_error_message": error_message,
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
    if _pending_otp_challenge(request) is not None:
        return redirect_to("/login/otp")
    setup_mode = not await user_repository.exists_any(session)
    safe_next_url = safe_next(str(request.query_params.get("next", "/")) or "/")
    return render_template(
        request,
        "login.html",
        current_user=None,
        context={
            "safe_next_url": safe_next_url,
            "setup_mode": setup_mode,
            "passkey_login_available": not setup_mode and await passkey_service.any_registered(session),
            "password_policy_min_length": PASSWORD_MIN_LENGTH,
            "password_policy_message": PASSWORD_POLICY_MESSAGE,
            **_captcha_context(),
        },
    )


@router.post("/setup")
@limiter.limit("10/minute")
async def setup_action(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    if await user_repository.exists_any(session):
        return redirect_to("/login")

    form = await validated_csrf_form(request)
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm_password", ""))

    def _error(message: str, *, status_code: int = 422) -> HTMLResponse:
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
                **_captcha_context(),
            },
            status_code=status_code,
        )

    captcha_outcome = verify_captcha_token(
        token=str(form.get("captcha_token", "")),
        honeypot=str(form.get(HONEYPOT_FIELD_NAME, "")),
        secret_key=get_settings().secret_key.get_secret_value(),
    )
    if captcha_outcome is not CaptchaOutcome.OK:
        log_authentication_failure(
            request,
            username=None,
            reason="anti_bot_rejected",
            status_code=403,
        )
        return _error(_CAPTCHA_REJECTED_MESSAGE, status_code=403)
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
    initialize_user_session(request, user)
    logger.info("First admin account created via setup UI: username=%r", user.username)
    push_flash(request, "success", f"Welcome, {user.username}. Your admin account has been created.")
    return redirect_to("/onboarding")


@router.post("/login")
@limiter.limit("5/minute;20/hour")
async def login_action(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    form = await validated_csrf_form(request)
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    next_path = str(form.get("next", "/")) or "/"
    captcha_outcome = verify_captcha_token(
        token=str(form.get("captcha_token", "")),
        honeypot=str(form.get(HONEYPOT_FIELD_NAME, "")),
        secret_key=get_settings().secret_key.get_secret_value(),
    )
    if captcha_outcome is not CaptchaOutcome.OK:
        log_authentication_failure(
            request,
            username=None,
            reason="anti_bot_rejected",
            status_code=403,
        )
        return await _render_login_failure(
            request,
            session,
            next_path=next_path,
            message=_CAPTCHA_REJECTED_MESSAGE,
        )
    if len(username) > _MAX_USERNAME_LENGTH:
        log_authentication_failure(
            request,
            username=None,
            reason="username_too_long",
            status_code=403,
        )
        return await _render_login_failure(
            request,
            session,
            next_path=next_path,
        )
    if len(password) > _MAX_PASSWORD_LENGTH:
        log_authentication_failure(
            request,
            username=username,
            reason="password_too_long",
            status_code=403,
        )
        return await _render_login_failure(
            request,
            session,
            next_path=next_path,
        )
    logger.debug("Login attempt for username=%r", username)
    user = await auth_service.authenticate(session, username, password, update_last_login=False)
    if user is None:
        log_authentication_failure(
            request,
            username=username,
            reason="invalid_credentials",
            status_code=403,
        )
        return await _render_login_failure(
            request,
            session,
            next_path=next_path,
        )
    if user.otp_enabled:
        initialize_pending_otp_session(request, user, next_path=safe_next(next_path))
        return redirect_to("/login/otp")
    await user_repository.update_last_login(session, user, datetime.now(UTC))
    initialize_user_session(request, user)
    await commit_and_flash(
        session,
        request,
        flashes=(("success", f"Welcome back, {user.username}."),),
    )
    return redirect_to(safe_next(next_path))


@router.get("/login/otp", response_class=HTMLResponse)
async def otp_login_page(request: Request) -> Response:
    challenge = _pending_otp_challenge(request)
    if challenge is None:
        return redirect_to("/login")
    _user_id, next_path, _fingerprint = challenge
    return _render_otp_challenge(request, next_path=next_path)


@router.post("/login/otp")
@limiter.limit("5/minute;20/hour")
async def otp_login_action(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    challenge = _pending_otp_challenge(request)
    if challenge is None:
        return redirect_to("/login")
    user_id, next_path, challenge_fingerprint = challenge
    form = await validated_csrf_form(request)
    code = str(form.get("code", ""))
    if len(code) > _MAX_OTP_CODE_LENGTH:
        code = ""
    user = await user_repository.get_by_id(session, user_id)
    if user is None or not hmac.compare_digest(challenge_fingerprint, user_session_fingerprint(user)):
        request.session.clear()
        return redirect_to("/login")
    method = await auth_service.verify_otp_factor(session, user, code)
    if method is None:
        log_authentication_failure(
            request,
            username=user.username if user is not None else None,
            reason="invalid_otp",
            status_code=403,
        )
        return _render_otp_challenge(
            request,
            next_path=next_path,
            status_code=403,
            error_message="Invalid authentication code.",
        )

    await user_repository.update_last_login(session, user, datetime.now(UTC))
    initialize_user_session(request, user)
    await commit_and_flash(
        session,
        request,
        flashes=(("success", f"Welcome back, {user.username}."),),
    )
    logger.info("OTP login completed using %s for username=%r", method, user.username)
    return redirect_to(next_path)


@router.post("/login/otp/cancel")
async def cancel_otp_login(request: Request) -> Response:
    """Discard a pending second-factor challenge so the sign-in form is reachable again."""
    await _validate_csrf_only(request)
    request.session.clear()
    return redirect_to("/login")


@router.post("/logout")
async def logout_action(request: Request, session: AsyncSession = Depends(get_db_session)) -> Response:
    await _validate_csrf_only(request)
    request.session.clear()
    return redirect_to("/login")
