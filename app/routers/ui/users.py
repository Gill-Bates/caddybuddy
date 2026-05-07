#!/usr/bin/env python3
#
# app/routers/ui/users.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

# User management routes (admin only).
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.repositories.users import user_repository
from app.services.auth import WeakPasswordError, auth_service
from app.services.events import publish_resource_event

from ._common import audit_commit_and_flash, require_admin, validated_form

router = APIRouter()


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    context = {
        "page_title": "Users",
        "users": await user_repository.list_all(session),
    }
    return render_template(request, "users.html", current_user=current_user, context=context)


@router.post("/users")
@limiter.limit("10/minute")
async def create_user(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    form = await validated_form(request)
    username = str(form.get("username", "")).strip()
    email = str(form.get("email", "")).strip() or None
    password = str(form.get("password", ""))
    role = str(form.get("role", "user")).strip() or "user"
    if await user_repository.get_by_username(session, username):
        push_flash(request, "danger", "That username already exists.")
        return redirect_to("/users")
    if email and await user_repository.get_by_email(session, email):
        push_flash(request, "danger", "That email address already exists.")
        return redirect_to("/users")
    try:
        created_user = await auth_service.create_user(
            session,
            username=username,
            email=email,
            password=password,
            role=role,
        )
    except WeakPasswordError as exc:
        push_flash(request, "danger", str(exc))
        return redirect_to("/users")
    try:
        await audit_commit_and_flash(
            session,
            request,
            action="user_created",
            resource_type="user",
            resource_id=str(created_user.id),
            details={"username": created_user.username, "role": created_user.role},
            status_code=201,
            actor=current_user,
            flashes=(("success", f"User '{created_user.username}' created."),),
        )
        await publish_resource_event("user", "created", str(created_user.id))
    except IntegrityError:
        await session.rollback()
        push_flash(request, "danger", "That username or email address already exists.")
        return redirect_to("/users")
    return redirect_to("/users")
