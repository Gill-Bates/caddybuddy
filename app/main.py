#!/usr/bin/env python3
#
# app/main.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import logging
from contextlib import asynccontextmanager
from inspect import isawaitable
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from app.config.limiter import limiter, update_rate_limit_enabled
from app.config.settings import get_settings
from app.database.session import dispose_engine, get_session_factory, init_database
from app.dependencies.web import push_flash
from app.middleware.csrf import CSRFMiddleware, SecurityHeadersMiddleware
from app.routers.caddy_api import router as caddy_api_router
from app.routers.api import router as api_router
from app.routers.ui import router as ui_router
from app.services.auth import auth_service
from app.services.caddy import caddy_service
from app.services.caddyfile_manager import get_caddy_runtime_status, onboard_caddy
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

    try:
        push_flash(request, "danger", "Too many attempts. Please try again in a minute.")
    except AssertionError:
        logger.warning("Could not store rate-limit flash message because session is unavailable.")
    return RedirectResponse(url=_safe_rate_limit_redirect_path(request), status_code=303)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize persistent resources on startup and release them on shutdown."""
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    application.state.default_admin_created = False
    await init_database()

    session_factory = get_session_factory()

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

            caddy_status = await get_caddy_runtime_status(session)
            application.state.caddy_status = caddy_status

        if caddy_status.onboarding_required and settings.auto_onboard:
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
        elif caddy_status.onboarding_required:
            logger.warning(
                "Caddy onboarding required for %s.",
                caddy_status.caddyfile_path,
            )
        elif caddy_status.error:
            logger.error("Caddy startup degraded: %s", caddy_status.error)

        await ssllabs_service.startup(settings)
        yield
    finally:
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