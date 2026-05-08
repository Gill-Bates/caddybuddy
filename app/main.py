#!/usr/bin/env python3
#
# app/main.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import IntegrityError
from starlette.middleware.sessions import SessionMiddleware

from app.config.limiter import limiter
from app.config.settings import get_settings
from app.database.session import dispose_engine, get_session_factory, init_database
from app.dependencies.web import push_flash
from app.middleware.security import SecurityHeadersMiddleware
from app.routers.api import router as api_router
from app.routers.ui import router as ui_router
from app.services.auth import auth_service
from app.services.caddy import caddy_service
from app.services.server_status_monitor import run_server_status_monitor


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
    if parsed.query:
        path = f"{path}?{parsed.query}"

    if not path.startswith("/") or path.startswith("//") or path.startswith("/\\"):
        return fallback
    return path


async def _handle_rate_limit_exceeded(request: Request, exc: RateLimitExceeded):
    """Return JSON for API routes and flash+redirect for browser UI routes."""
    if request.url.path.startswith("/api/"):
        return _rate_limit_exceeded_handler(request, exc)

    push_flash(request, "danger", "Too many attempts. Please try again in a minute.")
    return RedirectResponse(url=_safe_rate_limit_redirect_path(request), status_code=303)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize persistent resources on startup and release them on shutdown."""
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.state.default_admin_created = False
    await init_database()
    server_status_stop_event = asyncio.Event()
    server_status_task: asyncio.Task[None] | None = None

    try:
        async with get_session_factory()() as session:
            try:
                async with session.begin():
                    created_admin = await auth_service.ensure_default_admin(
                        session,
                        username=settings.default_admin_username,
                        password=settings.default_admin_password.get_secret_value(),
                        email=settings.default_admin_email,
                    )
            except IntegrityError:
                logger.info("Default admin already initialized by a concurrent worker.")
                created_admin = None

            if created_admin is not None:
                app.state.default_admin_created = True
                logger.warning(
                    "Created default admin '%s'. Change the password immediately.",
                    settings.default_admin_username,
                )
        server_status_task = asyncio.create_task(
            run_server_status_monitor(server_status_stop_event, get_session_factory())
        )
        yield
    finally:
        server_status_stop_event.set()
        if server_status_task is not None:
            await server_status_task
        await caddy_service.aclose()
        await dispose_engine()


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application."""
    settings = get_settings()
    static_dir: Path = settings.base_dir / "app" / "static"
    favicon_path: Path = static_dir / "img" / "favicon.svg"
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _handle_rate_limit_exceeded)
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

    return app


app = create_app()