#!/usr/bin/env python3
#
# tests/test_ui_auth.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config.settings import get_settings


_ENV_OVERRIDES = {
    "CB_SECRET_KEY": "unit-test-secret-key",
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key",
    "CB_ADMIN_PASSWORD": "UnitTestPassword-123A",
    "CADDYBUDDY_ADMIN_PASSWORD": "UnitTestPassword-123A",
}
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}

for key, value in _ENV_OVERRIDES.items():
    os.environ[key] = value

get_settings.cache_clear()

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.middleware.csrf import CSRFMiddleware, SecurityHeadersMiddleware
from app.database.session import get_db_session
from app.routers.ui.auth import router as auth_router


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


class UIAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        for key, value in _ENV_OVERRIDES.items():
            os.environ[key] = value
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()

    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(CSRFMiddleware)
        app.add_middleware(SessionMiddleware, secret_key="unit-test-secret-key")
        app.add_middleware(SecurityHeadersMiddleware)
        app.mount("/static", StaticFiles(directory="/opt/caddybuddy/app/static"), name="static")
        app.include_router(auth_router)
        return app

    @staticmethod
    def _extract_csrf_token(html: str) -> str:
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if match is None:
            raise AssertionError("csrf_token input not found in login page")
        return match.group(1)

    @staticmethod
    async def _session_override():
        yield AsyncMock()

    def test_login_failure_returns_403_and_logs_single_warning(self) -> None:
        app = self._build_app()
        app.dependency_overrides[get_db_session] = self._session_override

        with (
            patch("app.routers.ui.auth.auth_service.authenticate", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.auth.logger.warning") as warning,
        ):
            with TestClient(app) as client:
                login_page = client.get("/login")
                csrf_token = self._extract_csrf_token(login_page.text)
                response = client.post(
                    "/login",
                    data={
                        "username": "admin",
                        "password": "wrong-password",
                        "next": "/",
                        "csrf_token": csrf_token,
                    },
                )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Invalid credentials.", response.text)
        warning.assert_called_once_with("Authentication failed for username=%r status_code=403", "admin")

    def test_successful_login_keeps_redirect_flow(self) -> None:
        app = self._build_app()
        app.dependency_overrides[get_db_session] = self._session_override
        user = SimpleNamespace(id=1, username="admin", password_hash="hashed-password")

        with (
            patch("app.routers.ui.auth.auth_service.authenticate", new=AsyncMock(return_value=user)),
            patch("app.routers.ui.auth.commit_and_flash", new=AsyncMock()) as commit_and_flash,
            patch("app.routers.ui.auth.initialize_user_session") as initialize_user_session,
        ):
            with TestClient(app) as client:
                login_page = client.get("/login")
                csrf_token = self._extract_csrf_token(login_page.text)
                response = client.post(
                    "/login",
                    data={
                        "username": "admin",
                        "password": "Password123!",
                        "next": "/sites",
                        "csrf_token": csrf_token,
                    },
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        initialize_user_session.assert_called_once_with(unittest.mock.ANY, 1, "hashed-password")
        commit_and_flash.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()