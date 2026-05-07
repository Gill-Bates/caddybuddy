#!/usr/bin/env python3
#
# app/main.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from app.config.limiter import limiter
from app.config.settings import get_settings
from app.database.session import dispose_engine, get_session_factory, init_database
from app.middleware.security import SecurityHeadersMiddleware
from app.routers.api import router as api_router
from app.routers.ui import router as ui_router
from app.services.auth import auth_service
from app.services.caddy import caddy_service


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize persistent resources on startup and release them on shutdown."""
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.state.default_admin_created = False
    await init_database()
    try:
        async with get_session_factory()() as session:
            try:
                created_admin = await auth_service.ensure_default_admin(
                    session,
                    username=settings.default_admin_username,
                    password=settings.default_admin_password.get_secret_value(),
                    email=settings.default_admin_email,
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            if created_admin is not None:
                app.state.default_admin_created = True
                logger.warning(
                    "Created default admin '%s'. Change the password immediately.",
                    settings.default_admin_username,
                )
        yield
    finally:
        await caddy_service.aclose()
        await dispose_engine()


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application."""
    settings = get_settings()
    static_dir: Path = settings.base_dir / "app" / "static"
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.default_admin_created = False
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        session_cookie=settings.session_cookie_name,
        max_age=settings.session_max_age_seconds,
        same_site="lax",
        https_only=settings.environment != "development",
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.include_router(ui_router)
    app.include_router(api_router)

    return app


app = create_app()