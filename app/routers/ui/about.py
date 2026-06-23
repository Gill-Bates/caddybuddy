#!/usr/bin/env python3
#
# app/routers/ui/about.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""About page: version, dependencies, changelog, and GitHub update checks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.dependencies.web import redirect_to, render_template
from app.services.about import check_for_updates, get_about_data
from ._common import require_onboarding_completed, require_user

router = APIRouter()


@router.get("/about", response_class=HTMLResponse)
async def about_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")

    onboarding_redirect = await require_onboarding_completed(session)
    if onboarding_redirect is not None:
        return onboarding_redirect

    about_data = await get_about_data()
    return render_template(
        request,
        "about.html",
        current_user=current_user,
        context={"page_title": "About", **about_data},
    )


@router.get("/about/check-updates")
async def about_check_updates(
    request: Request,
    force: bool = Query(False, description="Bypass the cache and query GitHub immediately"),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    current_user = await require_user(request, session)
    if current_user is None:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)

    # Forced checks bypass the 1-hour cache and reach the GitHub API, so restrict them to admins.
    effective_force = force and current_user.role == "admin"
    result = await check_for_updates(effective_force)
    return JSONResponse(dict(result))
