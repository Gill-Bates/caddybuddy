#!/usr/bin/env python3
#
# app/main.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import asyncio
import html
import logging
import re
import time
from contextlib import asynccontextmanager, suppress
from inspect import isawaitable
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.limiter import limiter, update_rate_limit_enabled
from app.config.settings import Settings, get_settings
from app.database.session import dispose_engine, get_session_factory, init_database
from app.dependencies.web import push_flash, render_template
from app.middleware.csrf import CSRFMiddleware, SecurityHeadersMiddleware
from app.middleware.session import RequestAwareSessionMiddleware
from app.routers.caddy_api import router as caddy_api_router
from app.routers.api import router as api_router
from app.routers.ui import router as ui_router
from app.services.caddy import caddy_service
from app.services.caddyfile_manager import (
    get_caddy_runtime_status,
    onboard_caddy,
    onboarding_succeeded,
    sync_caddy_configuration,
    sync_succeeded,
)
from app.services.events import event_bus
from app.services.runtime_settings import get_rate_limit_enabled
from app.services.ssllabs import ssllabs_service
from app.services.auth import PASSWORD_MIN_LENGTH, PASSWORD_POLICY_MESSAGE


logger = logging.getLogger(__name__)
_UNSAFE_REDIRECT_PATH_RE = re.compile(r"[\x00-\x1f\x7f\\]")
_STARTUP_RECONCILE_RETRYABLE_ERROR_CODES = {"caddy_admin_unavailable"}
_STARTUP_RECONCILE_BLOCKED_PATHS = (
    ("/api/caddy/onboard", {"POST"}),
    ("/api/caddy/sync", {"POST"}),
    ("/api/sites", {"POST"}),
    ("/api/sites/", {"PATCH", "DELETE"}),
    ("/caddyfile", {"POST"}),
    ("/caddyfile/onboard", {"POST"}),
    ("/sites", {"POST"}),
)


def _request_expects_json(request: Request) -> bool:
    accept = request.headers.get("accept", "").lower()
    requested_with = request.headers.get("x-requested-with", "").lower()
    return (
        request.url.path.startswith("/api/")
        or request.url.path.startswith("/caddy-api/")
        or "application/json" in accept
        or requested_with == "xmlhttprequest"
    )


def _is_caddy_startup_reconcile_blocked_request(request: Request) -> bool:
    method = request.method.upper()
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False

    path = request.url.path
    for prefix, methods in _STARTUP_RECONCILE_BLOCKED_PATHS:
        if method not in methods:
            continue
        if prefix.endswith("/") and path.startswith(prefix):
            return True
        if path == prefix:
            return True
    if path.startswith("/sites/") and path.endswith(("/delete", "/renew-certificate")):
        return method == "POST"
    return False


def _startup_reconcile_is_retryable(error_code: str | None) -> bool:
    return error_code in _STARTUP_RECONCILE_RETRYABLE_ERROR_CODES


async def _refresh_caddy_status(application: FastAPI, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        application.state.caddy_status = await get_caddy_runtime_status(session)


class StartupReconcileMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not getattr(request.app.state, "caddy_reconcile_ready", True) and _is_caddy_startup_reconcile_blocked_request(request):
            message = "Caddy startup synchronization is still running. Please retry in a moment."
            logger.warning("Blocked %s %s while Caddy startup synchronization is still running.", request.method, request.url.path)
            if _request_expects_json(request):
                return JSONResponse({"detail": message}, status_code=503)
            return HTMLResponse(f"<!doctype html><html><body><p>{html.escape(message)}</p></body></html>", status_code=503)

        return await call_next(request)


def _safe_rate_limit_redirect_path(request: Request) -> str:
    """Return a safe same-origin redirect path for UI rate-limit responses.

    Query strings are intentionally dropped to avoid reflecting attacker-controlled
    parameters into redirects.
    """
    referer = request.headers.get("referer")
    fallback = "/login" if request.url.path == "/login" else "/"
    if not referer:
        return fallback

    parsed = urlsplit(referer)
    if parsed.netloc and parsed.netloc != request.url.netloc:
        return fallback

    path = parsed.path if parsed.path.startswith("/") else fallback
    decoded_path = unquote(path)

    if (
        not decoded_path.startswith("/")
        or decoded_path.startswith("//")
        or decoded_path.startswith("/\\")
        or _UNSAFE_REDIRECT_PATH_RE.search(path)
        or _UNSAFE_REDIRECT_PATH_RE.search(decoded_path)
    ):
        return fallback
    return decoded_path


async def _handle_rate_limit_exceeded(request: Request, exc: RateLimitExceeded):
    """Return JSON for API routes and flash+redirect for browser UI routes."""
    if _request_expects_json(request):
        response = _rate_limit_exceeded_handler(request, exc)
        if isawaitable(response):
            return await response
        return response

    if request.url.path in {"/login", "/setup"}:
        if "session" not in request.scope:
            logger.warning("Rate-limit flash skipped: session unavailable.")
            return HTMLResponse(
                "<!doctype html><html><body><p>Too many attempts. Please wait a minute and try again.</p></body></html>",
                status_code=429,
            )
        push_flash(request, "danger", "Too many attempts. Please wait a minute and try again.")
        return render_template(
            request,
            "login.html",
            current_user=None,
            context={
                "safe_next_url": "/",
                "setup_mode": request.url.path == "/setup",
                "password_policy_min_length": PASSWORD_MIN_LENGTH,
                "password_policy_message": PASSWORD_POLICY_MESSAGE,
            },
            status_code=429,
        )

    if "session" in request.scope:
        push_flash(request, "danger", "Too many attempts. Please try again in a minute.")
    else:
        logger.warning("Rate-limit flash skipped: session unavailable.")
    return RedirectResponse(url=_safe_rate_limit_redirect_path(request), status_code=303)


async def _run_caddy_startup_reconcile(
    application: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Reconcile Caddy's running config with the stored configuration on startup.

    Caddy may start *after* CaddyBuddy (e.g. both come up together on a host
    reboot), so its Admin API can be briefly unreachable. Retry with backoff
    until Caddy is reachable, then onboard or force-sync exactly once. Without
    this retry a startup race leaves Caddy running the on-disk Caddyfile only,
    and any Admin-API-loaded state is never restored.
    """
    deadline = time.monotonic() + settings.caddy_startup_reconcile_timeout_seconds
    delay = 2.0
    while True:
        async with session_factory() as session:
            caddy_status = await get_caddy_runtime_status(session)
        application.state.caddy_status = caddy_status

        if caddy_status.onboarding_required and not settings.auto_onboard:
            logger.warning("Caddy onboarding required for %s.", caddy_status.caddyfile_path)
            return

        if caddy_status.admin_api_reachable and caddy_status.onboarding_required:
            async with session_factory() as session:
                onboarding_result = await onboard_caddy(session)
                if onboarding_succeeded(onboarding_result.status):
                    await session.commit()
                else:
                    await session.rollback()
            if onboarding_result.status == "onboarded":
                logger.info("Caddy onboarding completed successfully during startup.")
            elif onboarding_result.status == "already_managed":
                logger.info("Caddy onboarding already completed before startup reconcile.")
            else:
                if _startup_reconcile_is_retryable(onboarding_result.error_code) and time.monotonic() < deadline:
                    logger.warning(
                        "Caddy auto-onboarding failed transiently during startup; retrying: %s",
                        onboarding_result.error or onboarding_result.status,
                    )
                    logger.info("Caddy startup reconcile retrying in %.0fs.", delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 1.5, 15.0)
                    continue
                logger.error(
                    "Caddy auto-onboarding failed during startup: %s",
                    onboarding_result.error or onboarding_result.status,
                )
            await _refresh_caddy_status(application, session_factory)
            return

        if caddy_status.admin_api_reachable and caddy_status.managed:
            async with session_factory() as session:
                startup_sync = await sync_caddy_configuration(session, force=True)
                if sync_succeeded(startup_sync.status):
                    await session.commit()
                else:
                    await session.rollback()
            if startup_sync.synced:
                logger.info("Startup sync complete: pushed config to Caddy.")
            elif startup_sync.status == "no_change":
                logger.info("Startup sync: Caddy config already current.")
            else:
                if _startup_reconcile_is_retryable(startup_sync.error_code) and time.monotonic() < deadline:
                    logger.warning("Startup sync failed transiently; retrying: %s", startup_sync.error)
                    logger.info("Caddy startup reconcile retrying in %.0fs.", delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 1.5, 15.0)
                    continue
                logger.error("Startup sync failed: %s", startup_sync.error)
            await _refresh_caddy_status(application, session_factory)
            return

        # Caddy's Admin API is not reachable yet (likely still starting). Retry
        # until the deadline before giving up.
        if time.monotonic() >= deadline:
            await _refresh_caddy_status(application, session_factory)
            logger.error(
                "Caddy startup reconcile timed out after %ss; Admin API unreachable (%s). "
                "Sites are served from the on-disk Caddyfile only until the next sync.",
                settings.caddy_startup_reconcile_timeout_seconds,
                caddy_status.error or "unreachable",
            )
            return
        logger.info("Caddy Admin API not reachable yet; retrying startup sync in %.0fs.", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 15.0)


async def _reconcile_caddy_on_startup(
    application: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Run startup reconciliation and isolate unexpected failures from shutdown."""
    try:
        await _run_caddy_startup_reconcile(application, session_factory, settings)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Unexpected Caddy startup reconcile failure.")
    finally:
        application.state.caddy_reconcile_ready = True


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize persistent resources on startup and release them on shutdown."""
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await init_database()

    session_factory = get_session_factory()
    reconcile_task: asyncio.Task[None] | None = None

    try:
        async with session_factory() as session:
            rate_limit_enabled = await get_rate_limit_enabled(session)
            update_rate_limit_enabled(rate_limit_enabled)
            logger.info("Rate limiting %s", "enabled" if rate_limit_enabled else "disabled")

            application.state.caddy_status = await get_caddy_runtime_status(session)
            application.state.caddy_reconcile_ready = False

        # Reconcile Caddy in the background so startup does not block while
        # waiting for Caddy's Admin API to become reachable (startup race).
        reconcile_task = asyncio.create_task(
            _reconcile_caddy_on_startup(application, session_factory, settings),
            name="caddy-startup-reconcile",
        )
        application.state.caddy_reconcile_task = reconcile_task

        await ssllabs_service.startup(settings)
        yield
    finally:
        if reconcile_task is not None:
            reconcile_task.cancel()
            with suppress(asyncio.CancelledError):
                await reconcile_task
            application.state.caddy_reconcile_task = None
        await ssllabs_service.shutdown()
        await event_bus.shutdown()
        await caddy_service.aclose()
        await dispose_engine()


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application."""
    settings = get_settings()
    static_dir: Path = settings.base_dir / "app" / "static"
    if not static_dir.is_dir():
        raise RuntimeError(f"Static directory does not exist: {static_dir}")

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _handle_rate_limit_exceeded)
    # Middleware executes in reverse registration order (last added = outermost).
    # Required order (outer → inner): SecurityHeaders → Session → startup gate → CSRF.
    # CSRF reads request.session, so it must run inside RequestAwareSessionMiddleware.
    # Do not reorder the session/security/CSRF chain; the startup gate intentionally
    # sits between Session and CSRF so mutating requests are blocked before routes run.
    app.add_middleware(
        CSRFMiddleware,
    )
    app.add_middleware(
        StartupReconcileMiddleware,
    )
    app.add_middleware(
        RequestAwareSessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        session_cookie=settings.session_cookie_name,
        max_age=settings.session_max_age_seconds,
        same_site=settings.session_cookie_samesite,
        https_only=settings.session_https_only,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> RedirectResponse:
        return RedirectResponse(url="/static/img/favicon.svg", status_code=307)

    app.include_router(ui_router)
    app.include_router(api_router)
    app.include_router(caddy_api_router)

    return app


app = create_app()
