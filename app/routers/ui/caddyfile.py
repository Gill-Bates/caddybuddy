#!/usr/bin/env python3
#
# app/routers/ui/caddyfile.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.config.settings import get_settings
from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.services.caddy import caddy_service
from app.services.runtime_settings import get_caddy_config
from app.services.caddyfile_manager import (
    get_baseline_caddyfile,
    get_caddy_runtime_status,
    onboard_caddy,
    onboarding_succeeded,
    onboarding_result_should_commit,
    set_baseline_caddyfile,
    sync_succeeded,
    validate_and_deploy_full_caddyfile,
)
from app.services.events import publish_resource_event

from ._common import require_admin, require_onboarding_completed, require_user, validated_form


router = APIRouter()


async def _effective_caddyfile_baseline(session: AsyncSession) -> str:
    baseline = await get_baseline_caddyfile(session)
    if baseline.strip():
        return baseline

    return get_settings().caddy_baseline_caddyfile.strip()


@router.get("/caddyfile", response_class=HTMLResponse)
async def caddyfile_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    if current_user.role != "admin":
        push_flash(request, "danger", "Administrator access is required.")
        return redirect_to("/")

    onboarding_redirect = await require_onboarding_completed(session)
    if onboarding_redirect is not None:
        return onboarding_redirect

    caddy_status = await get_caddy_runtime_status(session)
    context = {
        "page_title": "Caddyfile",
        "caddyfile": await _effective_caddyfile_baseline(session),
        "onboarding_required": caddy_status.onboarding_required,
        "caddyfile_path": caddy_status.caddyfile_path,
        "admin_api_reachable": caddy_status.admin_api_reachable,
    }
    return render_template(request, "caddyfile.html", current_user=current_user, context=context)


@router.post("/caddyfile")
@limiter.limit("10/minute")
async def save_caddyfile(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    baseline_caddyfile = str(form.get("caddyfile", "")).strip()

    caddy_config = await get_caddy_config(session)
    valid, message = await caddy_service.validate_caddyfile(baseline_caddyfile or "", admin_url=caddy_config.admin_url)
    if baseline_caddyfile and not valid:
        push_flash(request, "danger", f"Invalid Caddyfile: {message}")
        return redirect_to("/caddyfile")

    await set_baseline_caddyfile(session, baseline_caddyfile)
    await session.flush()

    try:
        success, deploy_message = await validate_and_deploy_full_caddyfile(session)
    except Exception:
        await session.rollback()
        push_flash(request, "danger", "Caddyfile was not saved: deployment failed unexpectedly.")
        return redirect_to("/caddyfile")

    if not success:
        await session.rollback()
        push_flash(request, "danger", f"Caddyfile was not saved: {deploy_message}")
        return redirect_to("/caddyfile")

    await session.commit()
    push_flash(request, "success", "Caddyfile saved and deployed.")
    await publish_resource_event("caddyfile", "updated", "primary")
    return redirect_to("/caddyfile")


@router.post("/caddyfile/validate")
@limiter.limit("20/minute")
async def validate_caddyfile_only(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_user(request, session)
    if current_user is None:
        return JSONResponse({"valid": False, "message": "Authentication required."}, status_code=401)
    if current_user.role != "admin":
        return JSONResponse({"valid": False, "message": "Administrator access is required."}, status_code=403)

    form = await validated_form(request)
    baseline_caddyfile = str(form.get("caddyfile", "")).strip()

    if not baseline_caddyfile:
        return JSONResponse({"valid": True, "message": "Caddyfile is empty but syntactically valid."})

    caddy_config = await get_caddy_config(session)
    valid, message = await caddy_service.validate_caddyfile(baseline_caddyfile, admin_url=caddy_config.admin_url)

    if not valid:
        return JSONResponse({"valid": False, "message": message})

    # Normalize indentation with the local UI formatter.
    try:
        formatted_caddyfile = await caddy_service.format_caddyfile(baseline_caddyfile)
    except Exception:
        logger.warning("Local Caddyfile formatting failed.", exc_info=True)
        formatted_caddyfile = baseline_caddyfile

    return JSONResponse({
        "valid": True,
        "message": message,
        "formatted_caddyfile": formatted_caddyfile.strip(),
    })


@router.post("/caddyfile/onboard")
@limiter.limit("5/minute")
async def run_onboarding(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Execute Caddy onboarding to initialize managed Caddyfile block."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    result = await onboard_caddy(session)

    if not onboarding_result_should_commit(result.status):
        await session.rollback()
        push_flash(request, "danger", f"Onboarding failed: {result.error or result.status}")
        return redirect_to("/caddyfile")

    await session.commit()
    if result.status == "onboarded":
        push_flash(request, "success", "Onboarding complete! CaddyBuddy is now managing your Caddyfile.")
        await publish_resource_event("caddyfile", "onboarded", "primary")
    elif result.status == "already_managed":
        push_flash(request, "info", "Caddyfile is already managed by CaddyBuddy.")
    elif onboarding_succeeded(result.status) or sync_succeeded(result.status):
        push_flash(request, "success", "Caddyfile synchronization completed.")

    return redirect_to("/caddyfile")
