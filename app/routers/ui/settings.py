#!/usr/bin/env python3
#
# app/routers/ui/settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter, update_rate_limit_enabled
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.repositories.users import user_repository
from app.services.auth import WeakPasswordError, auth_service
from app.services.runtime_settings import (
    get_caddy_config,
    get_rate_limit_enabled,
    get_ssllabs_email,
    set_caddy_config,
    set_rate_limit_enabled,
    set_ssllabs_email,
)
from app.services.ssllabs import (
    clear_registration_status_cache,
    register_email_with_ssllabs,
)
from app.utils.ssllabs import mask_email

from ._common import require_admin, validated_form


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
):
    if _expects_json_response(request):
        return JSONResponse({"success": success, "message": message}, status_code=status_code)

    push_flash(request, "success" if success else "danger", message)
    return redirect_to("/settings")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/login")

    caddy_config = await get_caddy_config(session)
    rate_limit_enabled = await get_rate_limit_enabled(session)
    ssllabs_email = await get_ssllabs_email(session)
    masked_email = mask_email(ssllabs_email) if ssllabs_email else None

    context = {
        "caddy_api_url": caddy_config.admin_url,
        "caddyfile_path": caddy_config.caddyfile_path_str,
        "rate_limit_enabled": rate_limit_enabled,
        "ssllabs_email": ssllabs_email,
        "ssllabs_masked_email": masked_email,
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

    form = await validated_form(request)
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
async def update_ssllabs_settings(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    form = await validated_form(request)
    ssllabs_email = str(form.get("ssllabs_email", ""))

    try:
        previous_email = await get_ssllabs_email(session)
        await set_ssllabs_email(session, ssllabs_email)
    except ValueError as exc:
        return _settings_response(request, success=False, message=str(exc), status_code=400)

    await session.commit()
    new_email = ssllabs_email.strip().lower() or None
    if previous_email and previous_email != new_email:
        clear_registration_status_cache(previous_email)
    if new_email:
        clear_registration_status_cache(new_email)
        return _settings_response(request, success=True, message="SSL Labs email updated.")
    return _settings_response(request, success=True, message="SSL Labs email removed.")


@router.post("/settings/change-password", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/login")

    form = await validated_form(request)
    current_password = str(form.get("current_password", ""))
    new_password = str(form.get("new_password", ""))
    confirm_password = str(form.get("confirm_password", ""))

    if not current_password or not new_password or not confirm_password:
        push_flash(request, "danger", "All password fields are required.")
        return redirect_to("/settings")

    if new_password != confirm_password:
        push_flash(request, "danger", "New passwords do not match.")
        return redirect_to("/settings")

    # Verify current password
    verified = await auth_service.verify_password(current_password, current_user.password_hash)
    if not verified:
        push_flash(request, "danger", "Current password is incorrect.")
        return redirect_to("/settings")

    # Hash and update new password
    try:
        new_hash = await auth_service.hash_password(new_password)
    except WeakPasswordError as e:
        push_flash(request, "danger", str(e))
        return redirect_to("/settings")
    except ValueError as e:
        push_flash(request, "danger", str(e))
        return redirect_to("/settings")

    await user_repository.update_password(session, current_user, new_hash)
    await session.commit()

    push_flash(request, "success", "Password changed successfully.")
    return redirect_to("/settings")


@router.post("/settings/register-ssllabs", response_class=HTMLResponse)
@limiter.limit("3/hour")
async def register_ssllabs_email(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/login")

    settings = get_settings()
    email = await get_ssllabs_email(session)

    if not email:
        push_flash(request, "danger", "No SSL Labs email configured.")
        return redirect_to("/settings")

    try:
        registered = await register_email_with_ssllabs(
            email=email,
            api_base_url=settings.ssllabs_api_base_url,
        )
        if registered:
            push_flash(request, "success", "Successfully registered with SSL Labs API.")
        else:
            push_flash(request, "danger", "Registration failed.")
    except Exception:
        logger.exception("Unexpected SSL Labs registration failure.")
        push_flash(request, "danger", "Registration failed due to an unexpected error.")

    return redirect_to("/settings")
