#!/usr/bin/env python3
#
# tests/test_ui_caddyfile.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
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
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.database.session import get_db_session
from app.middleware.csrf import CSRFMiddleware, SecurityHeadersMiddleware
from app.routers.ui.caddyfile import router as caddyfile_router


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


class UICaddyfileTests(unittest.TestCase):
    def setUp(self) -> None:
        for key, value in _ENV_OVERRIDES.items():
            os.environ[key] = value
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()

    @staticmethod
    async def _session_override():
        yield AsyncMock()

    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(CSRFMiddleware)
        app.add_middleware(SessionMiddleware, secret_key="unit-test-secret-key")
        app.add_middleware(SecurityHeadersMiddleware)
        app.mount("/static", StaticFiles(directory="/opt/caddybuddy/app/static"), name="static")

        @app.get("/", name="home_page")
        async def _home_page() -> dict[str, str]:
            return {"status": "ok"}

        @app.get("/sites", name="sites_page")
        async def _sites_page() -> dict[str, str]:
            return {"status": "ok"}

        @app.post("/logout", name="logout_action")
        async def _logout_action() -> dict[str, str]:
            return {"status": "ok"}

        app.include_router(caddyfile_router)
        app.dependency_overrides[get_db_session] = self._session_override
        return app

    def test_caddyfile_page_disables_validate_button_when_empty(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.caddyfile.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.caddyfile.get_baseline_caddyfile", new=AsyncMock(return_value="")),
        ):
            with TestClient(app) as client:
                response = client.get("/caddyfile")

        self.assertEqual(response.status_code, 200)
        self.assertIn("data-caddyfile-config-form", response.text)
        self.assertIn('id="caddyfile-validate-btn"', response.text)
        self.assertIn('data-validate-error-prefix="Caddyfile invalid"', response.text)
        self.assertIn('data-validate-error-prefix="Caddyfile invalid" disabled aria-disabled="true"', response.text)
        self.assertIn('build ', response.text)

    def test_caddyfile_page_keeps_validate_button_enabled_when_content_exists(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.caddyfile.require_user", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.caddyfile.get_baseline_caddyfile",
                new=AsyncMock(return_value="{")
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/caddyfile")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="caddyfile-validate-btn"', response.text)
        self.assertNotIn('data-validate-error-prefix="Caddyfile invalid" disabled', response.text)
        self.assertIn('aria-disabled="false"', response.text)
        self.assertIn('build ', response.text)


if __name__ == "__main__":
    unittest.main()