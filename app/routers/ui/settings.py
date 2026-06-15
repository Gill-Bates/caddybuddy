#!/usr/bin/env python3
#
# app/routers/ui/settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter, update_rate_limit_enabled
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.dependencies.web import initialize_user_session, push_flash, redirect_to, render_template
from app.repositories.users import user_repository
from app.services.auth import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    PASSWORD_POLICY_MESSAGE,
    WeakPasswordError,
    auth_service,
)
from app.services.runtime_settings import (
    SSLLABS_RETENTION_DAY_VALUES,
    get_caddy_config,
    get_rate_limit_enabled,
    get_ssllabs_email,
    get_ssllabs_history_retention_days,
    set_caddy_config,
    set_rate_limit_enabled,
    set_ssllabs_email,
    set_ssllabs_history_retention_days,
)
from app.services.caddy_onboarding import reset_onboarding_state
from app.services.ssllabs import (
    check_email_registration_status,
    clear_registration_status_cache,
    register_email_with_ssllabs,
    ssllabs_service,
)
from app.utils.ssllabs import mask_email

from ._common import require_admin, require_onboarding_completed, validated_csrf_form


logger = logging.getLogger(__name__)
router = APIRouter()


def _expects_json_response(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    requested_with = request.headers.get("x-requested-with", "")
    return "application/json" in accept.lower() or requested_with.lower() == "xmlhttprequest"


def _settings_response(
    request: Request,
    *,
    success: bool,
    message: str,
    status_code: int = 200,
) -> Response:
    if _expects_json_response(request):
        return JSONResponse({"success": success, "message": message}, status_code=status_code)

    push_flash(request, "success" if success else "danger", message)
    return redirect_to("/settings")


@router.post("/settings/onboarding/restart", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def restart_onboarding_wizard(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    await validated_csrf_form(request)
    await reset_onboarding_state(session)
    await session.commit()
    if _expects_json_response(request):
        return JSONResponse({"success": True, "message": "Onboarding wizard restarted."})

    push_flash(request, "success", "Onboarding wizard restarted.")
    return redirect_to("/onboarding")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/login")

    onboarding_redirect = await require_onboarding_completed(session)
    if onboarding_redirect is not None:
        return onboarding_redirect

    caddy_config = await get_caddy_config(session)
    rate_limit_enabled = await get_rate_limit_enabled(session)
    ssllabs_email = await get_ssllabs_email(session)
    masked_email = mask_email(ssllabs_email) if ssllabs_email else None
    ssllabs_retention_days = await get_ssllabs_history_retention_days(session)

    ssllabs_is_registered: bool | None = None
    if ssllabs_email:
        try:
            async with asyncio.timeout(5):
                ssllabs_is_registered = await check_email_registration_status(ssllabs_email)
        except TimeoutError:
            logger.warning("Timed out pre-fetching SSL Labs registration status for settings page.")
        except Exception:
            logger.warning(
                "Could not pre-fetch SSL Labs registration status for settings page.",
                exc_info=True,
            )

    context = {
        "caddy_api_url": caddy_config.admin_url,
        "caddyfile_path": caddy_config.caddyfile_path_str,
        "rate_limit_enabled": rate_limit_enabled,
        "ssllabs_email": ssllabs_email,
        "ssllabs_masked_email": masked_email,
        "ssllabs_is_registered": ssllabs_is_registered,
        "ssllabs_retention_days": ssllabs_retention_days,
        "ssllabs_retention_values": list(SSLLABS_RETENTION_DAY_VALUES),
        "password_policy_min_length": PASSWORD_MIN_LENGTH,
        "password_policy_max_length": PASSWORD_MAX_LENGTH,
        "password_policy_message": PASSWORD_POLICY_MESSAGE,
    }

    return render_template(request, "settings.html", current_user=current_user, context=context)


@router.post("/settings/caddy", response_class=HTMLResponse)
async def update_caddy_settings(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    form = await validated_csrf_form(request)
    caddy_api_url = form.get("caddy_api_url", "")
    caddyfile_path = form.get("caddyfile_path", "")
    rate_limit_enabled = form.get("rate_limit_enabled") == "on"

    try:
        await set_caddy_config(
            session,
            api_url=str(caddy_api_url),
            caddyfile_path=str(caddyfile_path),
        )
        await set_rate_limit_enabled(session, rate_limit_enabled)
    except ValueError as exc:
        return _settings_response(request, success=False, message=str(exc), status_code=400)

    await session.commit()
    update_rate_limit_enabled(rate_limit_enabled)
    return _settings_response(request, success=True, message="Settings updated.")


@router.post("/settings/ssllabs", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def update_ssllabs_settings(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    form = await validated_csrf_form(request)
    ssllabs_email = str(form.get("ssllabs_email", ""))

    try:
        previous_email_raw = await get_ssllabs_email(session)
        previous_email = previous_email_raw.strip().lower() if previous_email_raw else None
        await set_ssllabs_email(session, ssllabs_email)
    except ValueError as exc:
        return _settings_response(request, success=False, message=str(exc), status_code=400)

    await session.commit()
    new_email = ssllabs_email.strip().lower() or None
    if previous_email and previous_email != new_email:
        clear_registration_status_cache(previous_email)
    if new_email:
        clear_registration_status_cache(new_email)
        try:
            async with asyncio.timeout(10):
                await ssllabs_service.startup()
        except TimeoutError:
            logger.warning("Timed out refreshing SSL Labs service after email update.")
            return _settings_response(
                request,
                success=False,
                message="SSL Labs email was saved, but the service refresh timed out.",
                status_code=504,
            )
        except Exception:
            logger.exception("Could not refresh SSL Labs service after email update.")
            return _settings_response(
                request,
                success=False,
                message="SSL Labs email was saved, but the service refresh failed.",
                status_code=500,
            )

        return _settings_response(request, success=True, message="SSL Labs email updated.")
    return _settings_response(request, success=True, message="SSL Labs email removed.")


@router.post("/settings/ssllabs-retention", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def update_ssllabs_retention(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    form = await validated_csrf_form(request)
    raw_value = str(form.get("retention_days", "")).strip()
    try:
        retention_days = int(raw_value)
    except ValueError:
        return _settings_response(
            request, success=False, message="Retention value must be a whole number of days.", status_code=400
        )

    try:
        await set_ssllabs_history_retention_days(session, retention_days)
    except ValueError as exc:
        return _settings_response(request, success=False, message=str(exc), status_code=400)

    await session.commit()
    return _settings_response(request, success=True, message=f"SSL Labs history retention set to {retention_days} days.")


@router.post("/settings/change-password", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    form = await validated_csrf_form(request)
    current_password = str(form.get("current_password", ""))
    new_password = str(form.get("new_password", ""))
    confirm_password = str(form.get("confirm_password", ""))

    if not current_password or not new_password or not confirm_password:
        return _settings_response(request, success=False, message="All password fields are required.", status_code=400)

    if new_password != confirm_password:
        return _settings_response(request, success=False, message="New passwords do not match.", status_code=400)

    verified = await auth_service.verify_password(current_password, current_user.password_hash)
    if not verified:
        return _settings_response(request, success=False, message="Current password is incorrect.", status_code=400)

    try:
        new_hash = await auth_service.hash_password(new_password)
    except WeakPasswordError as e:
        return _settings_response(request, success=False, message=str(e), status_code=400)
    except ValueError as e:
        return _settings_response(request, success=False, message=str(e), status_code=400)

    await user_repository.update_password(session, current_user, new_hash)
    await session.commit()

    initialize_user_session(request, current_user.id, new_hash)
    return _settings_response(request, success=True, message="Password changed successfully.")


@router.post("/settings/register-ssllabs", response_class=HTMLResponse)
@limiter.limit("3/hour")
async def register_ssllabs_email(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    settings = get_settings()
    email = await get_ssllabs_email(session)

    if not email:
        return _settings_response(request, success=False, message="No SSL Labs email configured.", status_code=400)

    try:
        async with asyncio.timeout(10):
            registered = await register_email_with_ssllabs(
                email=email,
                api_base_url=settings.ssllabs_api_base_url,
            )
    except TimeoutError:
        logger.warning("Timed out registering SSL Labs email.")
        return _settings_response(
            request,
            success=False,
            message="Registration timed out.",
            status_code=504,
        )
    except Exception:
        logger.exception("Unexpected SSL Labs registration failure.")
        return _settings_response(
            request,
            success=False,
            message="Registration failed due to an unexpected error.",
            status_code=500,
        )

    if registered:
        return _settings_response(request, success=True, message="Successfully registered with SSL Labs API.")
    return _settings_response(request, success=False, message="Registration failed.", status_code=400)
