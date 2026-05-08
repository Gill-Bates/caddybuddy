#!/usr/bin/env python3
#
# app/routers/ui/profile.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from email.utils import parseaddr

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, refresh_session_timestamps, render_template
from app.repositories.users import user_repository
from app.services.auth import WeakPasswordError, auth_service

from ._common import audit_commit_and_flash, require_user, validated_form

router = APIRouter()

_MAX_USERNAME_LENGTH = 50
_MAX_EMAIL_LENGTH = 255
_MIN_PASSWORD_LENGTH = 12
_MAX_PASSWORD_LENGTH = 128


def _parse_profile_form(form: dict[str, object]) -> tuple[str, str | None]:
    username = str(form.get("username", "")).strip()
    email = str(form.get("email", "")).strip() or None

    if not username or len(username) > _MAX_USERNAME_LENGTH:
        raise ValueError(
            f"Username is required and must be at most {_MAX_USERNAME_LENGTH} characters."
        )
    if email is not None:
        parsed_email = parseaddr(email)[1]
        if not parsed_email or parsed_email != email or "@" not in parsed_email:
            raise ValueError("Please provide a valid email address.")
        if len(email) > _MAX_EMAIL_LENGTH:
            raise ValueError(
                f"Email address must be at most {_MAX_EMAIL_LENGTH} characters."
            )
    return username, email


def _parse_password_change_form(form: dict[str, object]) -> tuple[str, str]:
    current_password = str(form.get("current_password", ""))
    new_password = str(form.get("new_password", ""))
    confirm_password = str(form.get("confirm_password", ""))

    if len(current_password) > _MAX_PASSWORD_LENGTH or len(new_password) > _MAX_PASSWORD_LENGTH:
        raise ValueError("Password exceeds maximum allowed length.")
    if len(new_password) < _MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {_MIN_PASSWORD_LENGTH} characters long."
        )
    if new_password != confirm_password:
        raise ValueError("The new passwords do not match.")
    return current_password, new_password


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    context = {"page_title": "Profile"}
    return render_template(request, "profile.html", current_user=current_user, context=context)


@router.post("/profile")
@limiter.limit("5/minute")
async def update_profile(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await validated_form(request)
    try:
        username, email = _parse_profile_form(form)
    except ValueError as exc:
        push_flash(request, "danger", str(exc))
        return redirect_to("/profile")
    try:
        await user_repository.update_profile(session, current_user, username=username, email=email)
        await audit_commit_and_flash(
            session,
            request,
            action="profile_updated",
            resource_type="user",
            resource_id=str(current_user.id),
            details={"username": username, "email": email},
            status_code=303,
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
async def change_password(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await validated_form(request)
    try:
        current_password, new_password = _parse_password_change_form(form)
    except ValueError as exc:
        push_flash(request, "danger", str(exc))
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
        status_code=303,
        actor=current_user,
        flashes=(("success", "Password updated."),),
    )
    refresh_session_timestamps(request)
    return redirect_to("/profile")
