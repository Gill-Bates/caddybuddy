#!/usr/bin/env python3
#
# app/main.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from inspect import isawaitable
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.config.limiter import limiter, update_rate_limit_enabled
from app.config.settings import Settings, get_settings
from app.database.session import dispose_engine, get_session_factory, init_database
from app.dependencies.web import push_flash
from app.middleware.csrf import CSRFMiddleware, SecurityHeadersMiddleware
from app.routers.caddy_api import router as caddy_api_router
from app.routers.api import router as api_router
from app.routers.ui import router as ui_router
from app.services.auth import auth_service
from app.services.caddy import caddy_service
from app.services.caddyfile_manager import get_caddy_runtime_status, onboard_caddy, sync_caddy_configuration
from app.services.events import event_bus
from app.services.runtime_settings import get_rate_limit_enabled
from app.services.ssllabs import ssllabs_service


logger = logging.getLogger(__name__)


def _safe_rate_limit_redirect_path(request: Request) -> str:
    """Return a safe same-origin redirect target for UI rate-limit responses."""
    referer = request.headers.get("referer")
    fallback = "/login" if request.url.path == "/login" else "/"
    if not referer:
        return fallback

    parsed = urlsplit(referer)
    if parsed.netloc and parsed.netloc != request.url.netloc:
        return fallback

    path = parsed.path if parsed.path.startswith("/") else fallback

    if not path.startswith("/") or path.startswith("//") or path.startswith("/\\"):
        return fallback
    return path


async def _handle_rate_limit_exceeded(request: Request, exc: RateLimitExceeded):
    """Return JSON for API routes and flash+redirect for browser UI routes."""
    if request.url.path.startswith("/api/"):
        response = _rate_limit_exceeded_handler(request, exc)
        if isawaitable(response):
            return await response
        return response

    if "session" in request.scope:
        push_flash(request, "danger", "Too many attempts. Please try again in a minute.")
    else:
        logger.warning("Rate-limit flash skipped: session unavailable.")
    return RedirectResponse(url=_safe_rate_limit_redirect_path(request), status_code=303)


async def _reconcile_caddy_on_startup(
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
                async with session.begin():
                    onboarding_result = await onboard_caddy(session)
            if onboarding_result.status == "onboarded":
                logger.info("Caddy onboarding completed successfully during startup.")
            else:
                logger.error(
                    "Caddy auto-onboarding failed during startup: %s",
                    onboarding_result.error or onboarding_result.status,
                )
            async with session_factory() as session:
                application.state.caddy_status = await get_caddy_runtime_status(session)
            return

        if caddy_status.admin_api_reachable and caddy_status.managed:
            async with session_factory() as session:
                async with session.begin():
                    startup_sync = await sync_caddy_configuration(session, force=True)
            if startup_sync.synced:
                logger.info("Startup sync complete: pushed config to Caddy.")
            elif startup_sync.status == "no_change":
                logger.info("Startup sync: Caddy config already current.")
            else:
                logger.error("Startup sync failed: %s", startup_sync.error)
            async with session_factory() as session:
                application.state.caddy_status = await get_caddy_runtime_status(session)
            return

        # Caddy's Admin API is not reachable yet (likely still starting). Retry
        # until the deadline before giving up.
        if time.monotonic() >= deadline:
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


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize persistent resources on startup and release them on shutdown."""
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    application.state.default_admin_created = False
    await init_database()

    session_factory = get_session_factory()
    reconcile_task: asyncio.Task[None] | None = None

    try:
        async with session_factory() as session:
            async with session.begin():
                created_admin = await auth_service.ensure_default_admin(
                    session,
                    username=settings.default_admin_username,
                    password=settings.default_admin_password.get_secret_value(),
                    email=settings.default_admin_email,
                )

            if created_admin is not None:
                application.state.default_admin_created = True
                logger.warning(
                    "Created default admin '%s'. Change the password immediately.",
                    settings.default_admin_username,
                )

        async with session_factory() as session:
            rate_limit_enabled = await get_rate_limit_enabled(session)
            update_rate_limit_enabled(rate_limit_enabled)
            logger.info("Rate limiting %s", "enabled" if rate_limit_enabled else "disabled")

            application.state.caddy_status = await get_caddy_runtime_status(session)

        # Reconcile Caddy in the background so startup does not block while
        # waiting for Caddy's Admin API to become reachable (startup race).
        reconcile_task = asyncio.create_task(
            _reconcile_caddy_on_startup(application, session_factory, settings)
        )
        application.state.caddy_reconcile_task = reconcile_task

        await ssllabs_service.startup(settings)
        yield
    finally:
        if reconcile_task is not None:
            reconcile_task.cancel()
            with suppress(asyncio.CancelledError):
                await reconcile_task
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
    # Required order (outer → inner): SecurityHeaders → Session → CSRF.
    # CSRF reads request.session, so it must run inside SessionMiddleware.
    # Do not reorder these three registrations.
    app.add_middleware(
        CSRFMiddleware,
    )
    app.add_middleware(
        SessionMiddleware,
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