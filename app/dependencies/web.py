#!/usr/bin/env python3
#
# app/dependencies/web.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from functools import cache
import hmac
import secrets
from datetime import datetime
from hashlib import sha256

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.models.entities import User
from app.repositories.users import user_repository
from app.services.build_info import get_build_info


settings = get_settings()
templates = Jinja2Templates(directory=settings.base_dir / "app" / "templates")


def _format_datetime(value: datetime | None) -> str:
    """Format a datetime in local time and return '-' for missing values."""
    if value is None:
        return "-"
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


templates.env.filters["datetimeformat"] = _format_datetime


@cache
def _csrf_secret() -> bytes:
    """Return a cached CSRF subkey derived from the application secret."""
    master_secret = settings.secret_key.get_secret_value().encode("utf-8")
    return hmac.new(master_secret, b"csrf-v1", sha256).digest()


def _csrf_hmac(token: str) -> str:
    """Return the hex HMAC-SHA256 of a CSRF token under the CSRF subkey."""
    return hmac.new(_csrf_secret(), token.encode("utf-8"), sha256).hexdigest()


async def get_session_user(request: Request, session: AsyncSession) -> User | None:
    """Return the authenticated user from session, or None if missing or stale."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    try:
        parsed_user_id = int(user_id)
    except (TypeError, ValueError):
        request.session.pop("user_id", None)
        return None
    user = await user_repository.get_by_id(session, parsed_user_id)
    if user is None:
        request.session.pop("user_id", None)
    return user


def ensure_csrf_token(request: Request) -> str:
    """Return the session-bound CSRF token in signed form for form rendering."""
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return f"{token}.{_csrf_hmac(token)}"


def validate_csrf_token(request: Request, submitted_token: str | None) -> None:
    """Verify a submitted CSRF token against the session-bound signed value."""
    session_token = request.session.get("csrf_token")
    if not session_token or not submitted_token:
        raise HTTPException(status_code=400, detail="Invalid CSRF token.")

    expected_token = f"{session_token}.{_csrf_hmac(session_token)}"
    if not hmac.compare_digest(submitted_token, expected_token):
        raise HTTPException(status_code=400, detail="Invalid CSRF token.")


def push_flash(request: Request, category: str, message: str) -> None:
    """Append a flash message to the current session."""
    flashes = list(request.session.get("flashes", []))
    flashes.append({"category": category, "message": message})
    request.session["flashes"] = flashes


def pop_flashes(request: Request) -> list[dict[str, str]]:
    """Return and clear flash messages from the current session."""
    flashes = list(request.session.get("flashes", []))
    request.session["flashes"] = []
    return flashes


def redirect_to(path: str) -> RedirectResponse:
    """Return a 303 redirect response for the given path."""
    return RedirectResponse(url=path, status_code=303)


def render_template(
    request: Request,
    template_name: str,
    *,
    current_user: User | None,
    context: dict | None = None,
    status_code: int = 200,
):
    """Render a template with protected framework context keys."""
    page_context = dict(context) if context else {}
    page_context.update({
        "request": request,
        "current_user": current_user,
        "csrf_token": ensure_csrf_token(request),
        "flashes": pop_flashes(request),
        "build_info": get_build_info(),
        "app_name": settings.app_name,
    })
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context=page_context,
        status_code=status_code,
    )