#!/usr/bin/env python3
#
# app/routers/passkeys.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""WebAuthn (passkey) ceremony endpoints.

Only the two ceremonies need JSON, because they are driven by
``navigator.credentials``. Listing and removing passkeys is server-rendered in
``app/routers/ui/settings.py``.

Enrollment requires an authenticated session; the CSRF middleware covers it
because it is a cookie-authenticated ``/api/`` request, so the browser must send
``X-CSRF-Token``. Sign-in is unauthenticated but still origin-bound twice over:
by the middleware's Origin check and, cryptographically, by the origin inside
``clientDataJSON``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import (
    get_session_user,
    initialize_pending_otp_session,
    initialize_user_session,
    safe_redirect_path,
)
from app.models.entities import User
from app.repositories.users import user_repository
from app.schemas.passkeys import (
    PasskeyAuthenticationFinishRequest,
    PasskeyLoginFinishResponse,
    PasskeyOptionsResponse,
    PasskeyRegistrationFinishRequest,
    PasskeyRegistrationFinishResponse,
    PasskeyRegistrationStartRequest,
)
from app.services.auth import auth_service
from app.services.passkeys import (
    PasskeyChallengeError,
    PasskeyConfigurationError,
    PasskeyError,
    PasskeyLimitError,
    PasskeyVerificationError,
    passkey_service,
)
from app.utils.security_logging import log_authentication_failure

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/passkeys", tags=["passkeys"])


async def _require_api_user(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> User:
    current_user = await get_session_user(request, session)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return current_user


def _ceremony_http_error(exc: PasskeyError, *, unauthenticated: bool) -> HTTPException:
    """Map a ceremony failure onto a status code without leaking internals."""
    if isinstance(exc, PasskeyConfigurationError):
        logger.error("Passkey ceremony rejected: %s", exc)
        return HTTPException(status_code=500, detail=str(exc))
    if isinstance(exc, PasskeyLimitError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PasskeyVerificationError) and unauthenticated:
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, (PasskeyChallengeError, PasskeyVerificationError)):
        return HTTPException(status_code=400, detail=str(exc))
    logger.error("Unexpected passkey ceremony failure.", exc_info=exc)
    return HTTPException(status_code=500, detail="The passkey operation failed.")


@router.post("/register/start", response_model=PasskeyOptionsResponse)
@limiter.limit("10/minute")
async def start_passkey_registration(
    request: Request,
    payload: PasskeyRegistrationStartRequest,
    current_user: User = Depends(_require_api_user),
    session: AsyncSession = Depends(get_db_session),
) -> PasskeyOptionsResponse:
    if not await auth_service.verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    try:
        options = await passkey_service.begin_registration(session, request, current_user)
    except PasskeyError as exc:
        await session.rollback()
        raise _ceremony_http_error(exc, unauthenticated=False) from exc
    await session.commit()
    return PasskeyOptionsResponse(options=options)


@router.post("/register/finish", response_model=PasskeyRegistrationFinishResponse)
@limiter.limit("10/minute")
async def finish_passkey_registration(
    request: Request,
    payload: PasskeyRegistrationFinishRequest,
    current_user: User = Depends(_require_api_user),
    session: AsyncSession = Depends(get_db_session),
) -> PasskeyRegistrationFinishResponse:
    try:
        passkey = await passkey_service.finish_registration(
            session,
            request,
            current_user,
            credential=payload.credential,
            device_name=payload.device_name,
        )
    except PasskeyError as exc:
        await session.rollback()
        raise _ceremony_http_error(exc, unauthenticated=False) from exc
    await session.commit()
    return PasskeyRegistrationFinishResponse(
        success=True,
        message=f"Passkey \u201c{passkey.device_name or 'Unnamed passkey'}\u201d added.",
    )


@router.post("/login/start", response_model=PasskeyOptionsResponse)
@limiter.limit("10/minute;60/hour")
async def start_passkey_login(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> PasskeyOptionsResponse:
    """Issue assertion options for a discoverable-credential sign-in.

    No account is identified here, so the response is identical whether or not
    the requester owns a passkey.
    """
    try:
        options = await passkey_service.begin_authentication(session, request)
    except PasskeyError as exc:
        await session.rollback()
        raise _ceremony_http_error(exc, unauthenticated=True) from exc
    await session.commit()
    return PasskeyOptionsResponse(options=options)


@router.post("/login/finish", response_model=PasskeyLoginFinishResponse)
@limiter.limit("5/minute;20/hour")
async def finish_passkey_login(
    request: Request,
    payload: PasskeyAuthenticationFinishRequest,
    session: AsyncSession = Depends(get_db_session),
) -> PasskeyLoginFinishResponse:
    """Verify an assertion and establish the session.

    A passkey replaces the password only. When the account also has TOTP
    enabled, the second factor is still required, exactly as after a correct
    password.
    """
    next_path = safe_redirect_path(payload.next_url)
    try:
        user = await passkey_service.finish_authentication(
            session,
            request,
            credential=payload.credential,
        )
    except PasskeyError as exc:
        await session.rollback()
        error = _ceremony_http_error(exc, unauthenticated=True)
        if error.status_code in {400, 401}:
            log_authentication_failure(
                request,
                username=None,
                reason="invalid_passkey",
                status_code=error.status_code,
            )
        raise error from exc

    if user.otp_enabled:
        await session.commit()
        initialize_pending_otp_session(request, user, next_path=next_path)
        return PasskeyLoginFinishResponse(
            success=True,
            message="Passkey verified. Enter your authentication code to continue.",
            redirect_url="/login/otp",
        )

    await user_repository.update_last_login(session, user, datetime.now(UTC))
    await session.commit()
    initialize_user_session(request, user)
    logger.info("Passkey sign-in completed for username=%r", user.username)
    return PasskeyLoginFinishResponse(
        success=True,
        message=f"Welcome back, {user.username}.",
        redirect_url=next_path,
    )
