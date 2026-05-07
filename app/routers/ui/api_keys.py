#!/usr/bin/env python3
#
# app/routers/ui/api_keys.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.models.entities import User
from app.repositories.api_keys import api_key_repository
from app.services.auth import auth_service
from app.services.events import publish_resource_event
from app.utils.parsing import parse_expires_days

from ._common import audit_commit_and_flash, load_api_keys, require_user, validated_form

router = APIRouter()

_API_KEY_NAME_MAX_LENGTH = 120


async def _render_api_keys_page(
    request: Request,
    session: AsyncSession,
    current_user: User,
    *,
    pending_api_key: str | None = None,
    status_code: int = 200,
):
    """Render the API keys page with optional pending key display."""
    context = {
        "page_title": "API Keys",
        "api_keys": await load_api_keys(session, current_user),
        "show_all": current_user.role == "admin",
        "pending_api_key": pending_api_key,
    }
    response = render_template(
        request,
        "api_keys.html",
        current_user=current_user,
        context=context,
        status_code=status_code,
    )
    if pending_api_key is not None:
        response.headers["Cache-Control"] = "private, no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    return await _render_api_keys_page(request, session, current_user)


@router.post("/api-keys")
@limiter.limit("10/minute")
async def create_api_key(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await validated_form(request)
    name = str(form.get("name", "")).strip()
    if not name or len(name) > _API_KEY_NAME_MAX_LENGTH:
        push_flash(
            request,
            "danger",
            f"API key name must be between 1 and {_API_KEY_NAME_MAX_LENGTH} characters.",
        )
        return redirect_to("/api-keys")
    permissions = {
        "read": form.get("perm_read") == "on",
        "write": form.get("perm_write") == "on",
        "delete": form.get("perm_delete") == "on",
    }
    expires_at = parse_expires_days(str(form.get("expires_days", "")).strip() or None)
    api_key, raw_key = await auth_service.create_api_key(
        session,
        user_id=current_user.id,
        name=name,
        permissions=permissions,
        expires_at=expires_at,
    )
    await audit_commit_and_flash(
        session,
        request,
        action="api_key_created",
        resource_type="api_key",
        resource_id=str(api_key.id),
        details={"name": api_key.name, "permissions": permissions},
        status_code=201,
        actor=current_user,
        flashes=(("success", "API key created. Copy it now. It will not be shown again."),),
    )
    await publish_resource_event("api_key", "created", str(api_key.id))
    return await _render_api_keys_page(
        request,
        session,
        current_user,
        pending_api_key=raw_key,
        status_code=201,
    )


@router.post("/api-keys/{api_key_id}/toggle")
async def toggle_api_key(request: Request, api_key_id: int, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    await validated_form(request)
    api_key = await api_key_repository.get_by_id(session, api_key_id)
    if api_key is None:
        push_flash(request, "danger", "API key not found.")
        return redirect_to("/api-keys")
    if current_user.role != "admin" and api_key.user_id != current_user.id:
        push_flash(request, "danger", "You cannot modify that API key.")
        return redirect_to("/api-keys")
    is_active = not api_key.is_active
    await api_key_repository.set_active(session, api_key, is_active)
    await audit_commit_and_flash(
        session,
        request,
        action="api_key_toggled",
        resource_type="api_key",
        resource_id=str(api_key.id),
        details={"active": is_active},
        status_code=200,
        actor=current_user,
        flashes=(("success", f"API key {'enabled' if is_active else 'disabled'}."),),
    )
    await publish_resource_event("api_key", "updated", str(api_key.id))
    return redirect_to("/api-keys")
