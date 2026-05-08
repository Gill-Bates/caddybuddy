#!/usr/bin/env python3
#
# app/routers/ui/profile.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

# User profile routes.
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, refresh_session_timestamps, render_template
from app.repositories.users import user_repository
from app.services.auth import WeakPasswordError, auth_service

from ._common import audit_commit_and_flash, require_user, validated_form

router = APIRouter()

_MAX_PASSWORD_LENGTH = 128


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    context = {"page_title": "Profile"}
    return render_template(request, "profile.html", current_user=current_user, context=context)


@router.post("/profile")
async def update_profile(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await validated_form(request)
    username = str(form.get("username", "")).strip()
    email = str(form.get("email", "")).strip() or None
    try:
        await user_repository.update_profile(session, current_user, username=username, email=email)
        await audit_commit_and_flash(
            session,
            request,
            action="profile_updated",
            resource_type="user",
            resource_id=str(current_user.id),
            details={"username": username, "email": email},
            status_code=200,
            actor=current_user,
            flashes=(("success", "Profile updated."),),
        )
    except IntegrityError:
        await session.rollback()
        push_flash(request, "danger", "That username or email address is already in use.")
        return redirect_to("/profile")
    return redirect_to("/profile")


@router.post("/profile/password")
@limiter.limit("5/minute")
async def change_password(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await validated_form(request)
    current_password = str(form.get("current_password", ""))
    new_password = str(form.get("new_password", ""))
    confirm_password = str(form.get("confirm_password", ""))
    if len(current_password) > _MAX_PASSWORD_LENGTH or len(new_password) > _MAX_PASSWORD_LENGTH:
        push_flash(request, "danger", "Password exceeds maximum allowed length.")
        return redirect_to("/profile")
    if new_password != confirm_password:
        push_flash(request, "danger", "The new passwords do not match.")
        return redirect_to("/profile")
    if not await auth_service.verify_password(current_password, current_user.password_hash):
        push_flash(request, "danger", "Your current password is incorrect.")
        return redirect_to("/profile")
    try:
        await auth_service.update_password(
            session,
            actor=current_user,
            user=current_user,
            new_password=new_password,
        )
    except WeakPasswordError as exc:
        push_flash(request, "danger", str(exc))
        return redirect_to("/profile")
    await audit_commit_and_flash(
        session,
        request,
        action="password_changed",
        resource_type="user",
        resource_id=str(current_user.id),
        status_code=200,
        actor=current_user,
        flashes=(("success", "Password updated."),),
    )
    refresh_session_timestamps(request)
    return redirect_to("/profile")
