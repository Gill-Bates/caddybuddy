#!/usr/bin/env python3
#
# app/dependencies/web.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from base64 import b64encode
from functools import cache
import hmac
import secrets
import time
from datetime import UTC, datetime
from hashlib import sha256, sha384

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.templating import _TemplateResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.models.entities import User
from app.repositories.users import user_repository
from app.services.build_info import get_build_info


settings = get_settings()
templates = Jinja2Templates(directory=settings.base_dir / "app" / "templates")
_STATIC_DIR = (settings.base_dir / "app" / "static").resolve()


def _format_datetime(value: datetime | None) -> str:
    """Format a datetime in local time and return '-' for missing values."""
    if value is None:
        return "-"
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone().strftime("%Y-%m-%d %H:%M")


def _days_until(value: datetime | None) -> int | None:
    """Return calendar days until ``value`` in local time, or ``None`` if missing."""
    if value is None:
        return None
    now = datetime.now(UTC).astimezone()
    return (value.astimezone().date() - now.date()).days


def _expiry_badge_class(value: datetime | None) -> str:
    """Return the Bootstrap badge class for an expiration timestamp."""
    days_until = _days_until(value)
    if days_until is None:
        return "text-bg-secondary"
    if days_until <= 0:
        return "text-bg-danger"
    if days_until <= 3:
        return "text-bg-warning"
    return "text-bg-success"


templates.env.filters["datetimeformat"] = _format_datetime
templates.env.filters["daysuntil"] = _days_until
templates.env.filters["expirybadgeclass"] = _expiry_badge_class


@cache
def _csrf_secret() -> bytes:
    """Return a cached CSRF subkey derived from the application secret."""
    master_secret = settings.secret_key.get_secret_value().encode("utf-8")
    return hmac.new(master_secret, b"csrf-v1", sha256).digest()


def _csrf_hmac(token: str) -> str:
    """Return the hex HMAC-SHA256 of a CSRF token under the CSRF subkey."""
    return hmac.new(_csrf_secret(), token.encode("utf-8"), sha256).hexdigest()


@cache
def _asset_integrity_cached(relative_path: str, mtime_ns: int, size: int) -> str:
    """Return a cached SHA-384 SRI value for a specific file version."""
    del mtime_ns, size
    asset_path = (_STATIC_DIR / relative_path).resolve()
    try:
        asset_path.relative_to(_STATIC_DIR)
    except ValueError as exc:
        raise ValueError(f"Static asset path escapes static directory: {relative_path}") from exc

    digest = sha384(asset_path.read_bytes()).digest()
    return f"sha384-{b64encode(digest).decode('ascii')}"


def asset_integrity(relative_path: str) -> str:
    """Return a SHA-384 SRI value for a static asset under app/static."""
    return _asset_integrity_live(relative_path)


@cache
def _asset_integrity_by_path(relative_path: str) -> str:
    """Return a process-cached SHA-384 SRI value for immutable production assets."""
    asset_path = (_STATIC_DIR / relative_path).resolve()
    try:
        asset_path.relative_to(_STATIC_DIR)
    except ValueError as exc:
        raise ValueError(f"Static asset path escapes static directory: {relative_path}") from exc

    digest = sha384(asset_path.read_bytes()).digest()
    return f"sha384-{b64encode(digest).decode('ascii')}"


def _asset_integrity_live(relative_path: str) -> str:
    """Return a SHA-384 SRI value that tracks on-disk asset changes during reload mode."""
    asset_path = (_STATIC_DIR / relative_path).resolve()
    try:
        asset_path.relative_to(_STATIC_DIR)
    except ValueError as exc:
        raise ValueError(f"Static asset path escapes static directory: {relative_path}") from exc

    stat = asset_path.stat()
    return _asset_integrity_cached(relative_path, stat.st_mtime_ns, stat.st_size)


def _user_session_fingerprint(password_hash: str) -> str:
    """Derive a short HMAC fingerprint from the user's password hash.

    This binds each browser session to a specific database record.  After a DB
    reset the admin is recreated with a fresh bcrypt salt, so the fingerprint
    changes and any old session cookies are automatically rejected.
    """
    master_secret = settings.secret_key.get_secret_value().encode("utf-8")
    digest = hmac.new(master_secret, password_hash.encode("utf-8"), sha256).hexdigest()
    return digest[:16]


async def get_session_user(request: Request, session: AsyncSession) -> User | None:
    """
    Return the authenticated user from session, or None if missing or expired.

    Session expiration rules:
    - Inactivity timeout: Session expires after 60 min of no activity.
    - Absolute timeout: Session expires after 24h regardless of activity.
    - Each request extends the inactivity window by 60 min.
    - Fingerprint mismatch: Session is rejected when the user's password hash
      no longer matches (e.g. after a database reset or password change).
    """
    user_id = request.session.get("user_id")
    if not user_id:
        return None

    now = time.time()
    created_at = request.session.get("session_created_at", 0)
    last_activity = request.session.get("session_last_activity", 0)

    # Check absolute timeout (24h since login)
    if created_at and (now - created_at) > settings.session_absolute_timeout_seconds:
        request.session.clear()
        return None

    # Check inactivity timeout (60 min since last request)
    if last_activity and (now - last_activity) > settings.session_inactivity_timeout_seconds:
        request.session.clear()
        return None

    try:
        parsed_user_id = int(user_id)
    except (TypeError, ValueError):
        request.session.clear()
        return None

    user = await user_repository.get_by_id(session, parsed_user_id)
    if user is None:
        request.session.clear()
        return None

    # Reject sessions whose fingerprint does not match the current password hash.
    # This catches DB resets (bcrypt salt changes) and password changes.
    stored_fingerprint = request.session.get("user_fingerprint")
    expected_fingerprint = _user_session_fingerprint(user.password_hash)
    if stored_fingerprint != expected_fingerprint:
        request.session.clear()
        return None

    # Update last activity timestamp (extends session by inactivity timeout)
    request.session["session_last_activity"] = now
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
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")

    expected_token = f"{session_token}.{_csrf_hmac(session_token)}"
    if not hmac.compare_digest(submitted_token, expected_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")


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


def initialize_user_session(request: Request, user_id: int, password_hash: str) -> None:
    """Initialize a fresh authenticated user session.

    The ``password_hash`` is used to derive a fingerprint stored in the cookie.
    On each subsequent request the fingerprint is re-verified against the
    database, so sessions are automatically invalidated after a DB reset or
    password change.
    """
    now = time.time()
    request.session.clear()
    request.session.update({
        "user_id": user_id,
        "session_created_at": now,
        "session_last_activity": now,
        "user_fingerprint": _user_session_fingerprint(password_hash),
    })


def refresh_session_timestamps(request: Request) -> None:
    """Refresh session activity without resetting the absolute session age."""
    request.session["session_last_activity"] = time.time()


def render_template(
    request: Request,
    template_name: str,
    *,
    current_user: User | None,
    context: dict[str, object] | None = None,
    status_code: int = 200,
) -> _TemplateResponse:
    """Render a template with protected framework context keys."""
    page_context = dict(context) if context else {}
    page_context.update({
        "request": request,
        "current_user": current_user,
        "csrf_token": ensure_csrf_token(request),
        "asset_integrity": asset_integrity,
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