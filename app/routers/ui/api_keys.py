#!/usr/bin/env python3
#
# app/routers/ui/api_keys.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response
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
logger = logging.getLogger(__name__)

_API_KEY_NAME_MAX_LENGTH = 120
_API_KEYS_PAGE_LIMIT = 100
_EVENT_PUBLISH_TIMEOUT_SECONDS = 2.0


async def _publish_resource_event_best_effort(
    resource_type: str,
    action: str,
    resource_id: str,
) -> None:
    try:
        await asyncio.wait_for(
            publish_resource_event(resource_type, action, resource_id),
            timeout=_EVENT_PUBLISH_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.exception(
            "Failed to publish %s %s event for resource id %s",
            resource_type,
            action,
            resource_id,
        )


async def _render_api_keys_page(
    request: Request,
    session: AsyncSession,
    current_user: User,
    *,
    pending_api_key: str | None = None,
    status_code: int = 200,
) -> Response:
    """Render the API keys page with optional pending key display."""
    context = {
        "page_title": "API Keys",
        "api_keys": await load_api_keys(session, current_user, limit=_API_KEYS_PAGE_LIMIT),
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
        response.headers["Vary"] = "Cookie"
    return response


@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    return await _render_api_keys_page(request, session, current_user)


@router.post("/api-keys")
@limiter.limit("10/minute")
async def create_api_key(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
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
    if not any(permissions.values()):
        push_flash(request, "danger", "API key must have at least one permission.")
        return redirect_to("/api-keys")

    expires_days_raw = str(form.get("expires_days", "")).strip()
    if expires_days_raw:
        try:
            expires_days = int(expires_days_raw)
        except ValueError:
            push_flash(request, "danger", "Expiration must be a non-negative number of days.")
            return redirect_to("/api-keys")
        if expires_days < 0:
            push_flash(request, "danger", "Expiration must be a non-negative number of days.")
            return redirect_to("/api-keys")
    try:
        expires_at = parse_expires_days(expires_days_raw or None)
        if expires_at is not None and expires_at <= datetime.now(UTC):
            push_flash(request, "danger", "Expiration date must be in the future.")
            return redirect_to("/api-keys")
        api_key, raw_key = await auth_service.create_api_key(
            session,
            actor=current_user,
            user_id=current_user.id,
            name=name,
            permissions=permissions,
            expires_at=expires_at,
        )
    except ValueError as exc:
        push_flash(request, "danger", str(exc))
        return redirect_to("/api-keys")
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
    await _publish_resource_event_best_effort("api_key", "created", str(api_key.id))
    return await _render_api_keys_page(
        request,
        session,
        current_user,
        pending_api_key=raw_key,
        status_code=201,
    )


@router.post("/api-keys/{api_key_id}/toggle")
@limiter.limit("10/minute")
async def toggle_api_key(
    request: Request,
    api_key_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    await validated_form(request)
    toggle_result = await api_key_repository.toggle_active_for_actor(
        session,
        api_key_id=api_key_id,
        actor=current_user,
    )
    if toggle_result is None:
        push_flash(request, "danger", "API key not found.")
        return redirect_to("/api-keys")
    toggled_api_key_id, is_active = toggle_result
    await audit_commit_and_flash(
        session,
        request,
        action="api_key_toggled",
        resource_type="api_key",
        resource_id=str(toggled_api_key_id),
        details={"active": is_active},
        status_code=303,
        actor=current_user,
        flashes=(("success", f"API key {'enabled' if is_active else 'disabled'}."),),
    )
    await _publish_resource_event_best_effort("api_key", "updated", str(toggled_api_key_id))
    return redirect_to("/api-keys")
