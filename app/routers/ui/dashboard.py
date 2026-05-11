#!/usr/bin/env python3
#
# app/routers/ui/dashboard.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Dashboard route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.dependencies.web import redirect_to, render_template
from app.models.entities import User

from ._common import load_dashboard_context, require_user

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current_user: User | None = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    context: dict[str, object] = {
        "page_title": "Dashboard",
        **(await load_dashboard_context(session)),
    }
    return render_template(request, "dashboard.html", current_user=current_user, context=context)
