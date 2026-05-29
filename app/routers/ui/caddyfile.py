#!/usr/bin/env python3
#
# app/routers/ui/caddyfile.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.services.caddy import caddy_service
from app.services.caddyfile_manager import (
    get_baseline_caddyfile,
    get_caddy_runtime_status,
    onboard_caddy,
    set_baseline_caddyfile,
    validate_and_deploy_full_caddyfile,
)
from app.services.events import publish_resource_event

from ._common import require_admin, require_user, validated_form


router = APIRouter()


@router.get("/caddyfile", response_class=HTMLResponse)
async def caddyfile_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")

    caddy_status = await get_caddy_runtime_status(session)
    context = {
        "page_title": "Caddyfile",
        "caddyfile": await get_baseline_caddyfile(session),
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

    valid, message = await caddy_service.validate_caddyfile(baseline_caddyfile or "")
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

    form = await validated_form(request)
    baseline_caddyfile = str(form.get("caddyfile", "")).strip()

    if not baseline_caddyfile:
        return JSONResponse({"valid": True, "message": "Caddyfile is empty but syntactically valid."})

    valid, message = await caddy_service.validate_caddyfile(baseline_caddyfile)

    if not valid:
        return JSONResponse({"valid": False, "message": message})

    # Format caddyfile using Caddy's formatter
    try:
        formatted_caddyfile = await caddy_service.format_caddyfile(baseline_caddyfile)
    except Exception:
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
    await session.commit()

    if result.status == "onboarded":
        push_flash(request, "success", "Onboarding complete! CaddyBuddy is now managing your Caddyfile.")
    elif result.status == "already_managed":
        push_flash(request, "info", "Caddyfile is already managed by CaddyBuddy.")
    else:
        push_flash(request, "danger", f"Onboarding failed: {result.error or result.status}")

    await publish_resource_event("caddyfile", "onboarded", "primary")
    return redirect_to("/caddyfile")
