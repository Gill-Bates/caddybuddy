#!/usr/bin/env python3
#
# tests/test_ui_sites.py
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
from app.routers.ui.sites import router as sites_router


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


class UISitesTests(unittest.TestCase):
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

        @app.get("/caddyfile", name="caddyfile_page")
        async def _caddyfile_page() -> dict[str, str]:
            return {"status": "ok"}

        @app.post("/logout", name="logout_action")
        async def _logout_action() -> dict[str, str]:
            return {"status": "ok"}

        app.include_router(sites_router)
        app.dependency_overrides[get_db_session] = self._session_override
        return app

    def test_sites_page_renders_config_textarea_and_status_toggle(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=1,
            domain="example.com",
            enabled=False,
            caddy_directives="reverse_proxy backend:8080",
        )

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[site])),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
        ):
            with TestClient(app) as client:
                response = client.get("/sites/1")

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="caddy_directives"', response.text)
        self.assertIn("Site-specific Caddy directives.", response.text)
        self.assertNotIn("Upstream URL", response.text)
        self.assertIn('role="switch"', response.text)
        self.assertNotIn(">Disabled</span>", response.text)
        self.assertIn("site-enabled-label", response.text)
        self.assertIn(">Enabled</label>", response.text)
        self.assertIn("data-site-config-form", response.text)
        self.assertIn("data-domain-tag-input", response.text)
        self.assertIn("data-domain-tag-entry", response.text)
        self.assertIn("data-existing-domains=", response.text)
        self.assertIn("data-site-save-button disabled", response.text)
        self.assertIn("data-validate-error-prefix=\"Site configuration invalid\" disabled", response.text)

    def test_create_sites_page_renders_validate_and_save_disabled_initially(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[])),
        ):
            with TestClient(app) as client:
                response = client.get("/sites")

        self.assertEqual(response.status_code, 200)
        self.assertIn("data-site-save-button disabled", response.text)
        self.assertIn("data-validate-error-prefix=\"Site configuration invalid\" disabled", response.text)


if __name__ == "__main__":
    unittest.main()