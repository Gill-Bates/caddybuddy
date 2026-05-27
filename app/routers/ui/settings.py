#!/usr/bin/env python3
#
# app/routers/ui/settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.repositories.users import user_repository
from app.services.auth import AuthService, WeakPasswordError
from app.services.ssllabs import (
    check_email_registration_status,
    register_email_with_ssllabs,
    SslLabsRegistrationError,
)
from app.utils.ssllabs import mask_email

from ._common import require_admin, require_user, validated_form


router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/login")

    settings = get_settings()
    ssllabs_email = getattr(settings, "ssllabs_email", None)
    masked_email = mask_email(ssllabs_email) if ssllabs_email else None

    context = {
        "ssllabs_email": ssllabs_email,
        "ssllabs_masked_email": masked_email,
    }

    return render_template(request, "settings.html", current_user=current_user, context=context)


@router.post("/settings/change-password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/login")

    async with validated_form(request) as form:
        current_password = form.get("current_password", "").strip()
        new_password = form.get("new_password", "").strip()
        confirm_password = form.get("confirm_password", "").strip()

    if not current_password or not new_password or not confirm_password:
        push_flash(request, "danger", "All password fields are required.")
        return redirect_to("/settings")

    if new_password != confirm_password:
        push_flash(request, "danger", "New passwords do not match.")
        return redirect_to("/settings")

    # Verify current password
    auth_service = AuthService()
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
async def register_ssllabs_email(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/login")

    settings = get_settings()
    email = getattr(settings, "ssllabs_email", None)

    if not email:
        push_flash(request, "danger", "No SSL Labs email configured. Set CB_SSLLABS_EMAIL environment variable.")
        return redirect_to("/settings")

    try:
        await register_email_with_ssllabs(
            email=email,
            api_base_url=settings.ssllabs_api_base_url,
        )
        push_flash(request, "success", "Successfully registered with SSL Labs API.")
    except SslLabsRegistrationError as e:
        push_flash(request, "danger", f"Registration failed: {e}")
    except Exception as e:
        push_flash(request, "danger", f"Registration failed: {e}")

    return redirect_to("/settings")
