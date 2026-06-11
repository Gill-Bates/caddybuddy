#!/usr/bin/env python3
#
# app/routers/ui/dashboard.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Root route for the simplified UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.dependencies.web import redirect_to, render_template
from app.services.caddyfile_manager import get_caddy_runtime_status
from app.services.dashboard import get_dashboard_shell_metrics
from ._common import require_user

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
@router.get("/home", response_class=HTMLResponse)
async def home_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")

    metrics = await get_dashboard_shell_metrics(session)
    runtime_status = await get_caddy_runtime_status(session, check_admin_api=False)
    return render_template(
        request,
        "home.html",
        current_user=current_user,
        context={
            "page_title": "Home",
            "metrics": metrics,
            "runtime_status": runtime_status,
        },
    )
