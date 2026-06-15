#!/usr/bin/env python3
#
# app/routers/ui/onboarding.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""First-run Caddy onboarding wizard routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.services.caddy_onboarding import (
    derive_onboarding_mode,
    detect_runtime_location,
    enable_admin_api_and_reprobe,
    execute_onboarding,
    get_onboarding_state,
    get_onboarding_caddyfile_path_candidates,
    mode_to_choice,
    onboarding_caddy_locations,
    onboarding_caddy_sources,
    onboarding_runtime_locations,
    run_onboarding_preflight,
    save_onboarding_location,
    start_onboarding,
)
from app.services.runtime_settings import (
    get_caddy_config,
    get_ssllabs_email,
    suggest_caddyfile_path,
)
from app.config.settings import get_settings

from ._common import require_admin, validated_form


router = APIRouter()


@router.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    state = await get_onboarding_state(session)
    if state.status == "completed":
        return redirect_to("/")

    caddy_config = await get_caddy_config(session)
    ssllabs_email = await get_ssllabs_email(session)
    default_runtime_location = state.runtime_location or detect_runtime_location()
    settings = get_settings()
    suggested_caddyfile_path = (
        state.caddyfile_path
        or suggest_caddyfile_path(
            default_runtime_location,
            mounted_caddyfile_path=settings.mounted_caddyfile_path,
        )
    )
    caddyfile_candidates = get_onboarding_caddyfile_path_candidates(
        default_runtime_location,
    )
    runtime_location_labels = {
        location["value"]: location["label"]
        for location in onboarding_runtime_locations()
    }
    default_runtime_location_label = runtime_location_labels.get(
        default_runtime_location,
        default_runtime_location,
    )
    selected_location_from_mode, selected_source = mode_to_choice(state.mode)
    # Prefer the explicitly saved step-1 choice; fall back to what mode_to_choice derives
    # so navigating back to step 2 works for states that predate the 4-step split.
    pending_location = state.pending_location or selected_location_from_mode

    return render_template(
        request,
        "onboarding.html",
        current_user=current_user,
        context={
            "page_title": "Caddy Onboarding",
            "hide_sidebar": True,
            "state": state,
            "caddy_locations": onboarding_caddy_locations(),
            "caddy_sources": onboarding_caddy_sources(),
            "pending_location": pending_location,
            "selected_location": pending_location,
            "selected_source": selected_source,
            "runtime_locations": onboarding_runtime_locations(),
            "live_updates_enabled": False,
            "default_admin_api_url": state.admin_api_url or caddy_config.admin_url,
            "default_runtime_location": default_runtime_location,
            "default_runtime_location_label": default_runtime_location_label,
            "default_caddyfile_path": suggested_caddyfile_path,
            "caddyfile_candidates": caddyfile_candidates,
            "default_acme_email": state.acme_email or ssllabs_email or "",
        },
    )


@router.post("/onboarding/location")
@limiter.limit("10/minute")
async def onboarding_location_action(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    try:
        await save_onboarding_location(
            session,
            caddy_location=str(form.get("caddy_location", "")),
        )
    except ValueError as exc:
        await session.rollback()
        push_flash(request, "danger", str(exc))
        return redirect_to("/onboarding")

    await session.commit()
    return redirect_to("/onboarding")


@router.post("/onboarding/mode")
@limiter.limit("10/minute")
async def onboarding_mode_action(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    # The runtime location is auto-detected by the service; only honour an explicit
    # override if one is posted (none is sent by the wizard form).
    runtime_location_raw = form.get("runtime_location")
    runtime_location = str(runtime_location_raw) if runtime_location_raw else None
    # Step 1 asks two questions (where Caddy runs + what to start from); the service still
    # works on a single mode, so translate the answers here before persisting.
    try:
        mode = derive_onboarding_mode(
            str(form.get("caddy_location", "")),
            str(form.get("caddy_source", "")),
        )
        await start_onboarding(
            session,
            mode=mode,
            runtime_location=runtime_location,
        )
    except ValueError as exc:
        await session.rollback()
        push_flash(request, "danger", str(exc))
        return redirect_to("/onboarding")

    await session.commit()
    return redirect_to("/onboarding")


@router.post("/onboarding/preflight")
@limiter.limit("10/minute")
async def onboarding_preflight_action(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    try:
        state = await run_onboarding_preflight(
            session,
            admin_api_url=str(form.get("admin_api_url", "")),
            acme_email=str(form.get("acme_email", "")),
            caddyfile_path=str(form.get("caddyfile_path", "")),
        )
    except ValueError as exc:
        await session.rollback()
        push_flash(request, "danger", str(exc))
        return redirect_to("/onboarding")

    await session.commit()
    if state.preflight_errors:
        push_flash(request, "danger", "Preflight found issues. Review the fields below.")
    else:
        push_flash(request, "success", "Preflight passed. Review the summary before execution.")
    return redirect_to("/onboarding")


@router.post("/onboarding/enable-admin-api")
@limiter.limit("5/minute")
async def onboarding_enable_admin_api_action(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    # Enforce the confirmation server-side; the HTML `required` attribute is only client-side UX.
    if str(form.get("confirm_admin_api_enablement", "")) != "yes":
        push_flash(request, "danger", "Confirm that CaddyBuddy may modify the Caddyfile and restart Caddy.")
        return redirect_to("/onboarding")

    try:
        # The service owns all filesystem/restart rollback; only DB state is rolled back here.
        state = await enable_admin_api_and_reprobe(session)
    except ValueError as exc:
        await session.rollback()
        push_flash(request, "danger", str(exc))
        return redirect_to("/onboarding")

    await session.commit()
    if state.preflight_passed:
        push_flash(request, "success", "Admin API enabled. Review the summary before execution.")
    else:
        push_flash(request, "danger", state.error_message or "Could not enable the Caddy Admin API.")
    return redirect_to("/onboarding")


@router.post("/onboarding/execute")
@limiter.limit("5/minute")
async def onboarding_execute_action(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    try:
        state = await execute_onboarding(
            session,
            exclusive_manager_confirmed=str(form.get("exclusive_manager_confirmed", "")).lower() == "on",
        )
    except ValueError as exc:
        await session.rollback()
        push_flash(request, "danger", str(exc))
        return redirect_to("/onboarding")

    if state.status == "completed":
        await session.commit()
        push_flash(request, "success", "Caddy onboarding completed.")
        request.session["show_onboarding_confetti"] = True
        return redirect_to("/")

    await session.commit()
    push_flash(request, "danger", state.error_message or "Caddy onboarding failed.")
    return redirect_to("/onboarding")
