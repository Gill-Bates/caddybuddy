#!/usr/bin/env python3
#
# tests/ui_test_app.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.database.session import get_db_session
from app.middleware.csrf import CSRFMiddleware, SecurityHeadersMiddleware


type StubRouteMethod = Literal["GET", "POST"]
type StubRoute = tuple[StubRouteMethod, str, str]

_STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


def _add_stub_route(app: FastAPI, method: StubRouteMethod, path: str, name: str) -> None:
    async def _handler() -> dict[str, str]:
        return {"status": "ok"}

    if method == "GET":
        app.get(path, name=name)(_handler)
        return
    app.post(path, name=name)(_handler)


def build_ui_test_app(
    router,
    *,
    session_override: Callable[[], object],
    stub_routes: Iterable[StubRoute],
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="unit-test-secret-key-for-testing")
    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    for method, path, name in stub_routes:
        _add_stub_route(app, method, path, name)

    app.include_router(router)
    app.dependency_overrides[get_db_session] = session_override
    return app