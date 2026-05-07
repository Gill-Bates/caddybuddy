#!/usr/bin/env python3
#
# app/routers/ui/audit_logs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

# Audit log routes.
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.dependencies.web import get_session_user, redirect_to, render_template
from app.repositories.audit_logs import audit_log_repository

from ._common import require_admin

router = APIRouter()


def _format_log_entry(log) -> dict:
    """Format audit log entry for JSON response."""
    resource = log.resource_type
    if log.resource_id:
        resource = f"{resource} #{log.resource_id}"
    return {
        "id": log.id,
        "timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M") if log.timestamp else "",
        "timestamp_iso": log.timestamp.isoformat() if log.timestamp else "",
        "action": log.action,
        "username": log.username,
        "resource": resource,
        "details": log.details_json,
    }


@router.get("/audit-logs", response_class=HTMLResponse)
async def audit_logs_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    usernames = await audit_log_repository.get_distinct_usernames(session)
    context = {
        "page_title": "Audit Logs",
        "usernames": usernames,
    }
    return render_template(request, "audit_logs.html", current_user=current_user, context=context)


@router.get("/api/audit-logs", response_class=JSONResponse)
async def audit_logs_api(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    search: str = Query(default=""),
    username: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
):
    """
    Paginated audit log API with search and date filters.

    Returns JSON with entries array and metadata.
    """
    current_user = await get_session_user(request, session)
    if current_user is None or current_user.role != "admin":
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    parsed_date_from = None
    parsed_date_to = None

    if date_from:
        try:
            parsed_date_from = datetime.fromisoformat(date_from)
        except ValueError:
            pass

    if date_to:
        try:
            parsed_date_to = datetime.fromisoformat(f"{date_to}T23:59:59")
        except ValueError:
            pass

    entries, total = await audit_log_repository.list_paginated(
        session,
        offset=offset,
        limit=limit,
        search=search.strip() or None,
        username=username.strip() or None,
        date_from=parsed_date_from,
        date_to=parsed_date_to,
    )

    return JSONResponse({
        "entries": [_format_log_entry(e) for e in entries],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(entries) < total,
    })
