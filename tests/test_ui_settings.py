#!/usr/bin/env python3
#
# tests/test_ui_settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

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
from app.routers.ui.settings import router as settings_router


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


class UISettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        for key, value in _ENV_OVERRIDES.items():
            os.environ[key] = value
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()

    @staticmethod
    async def _session_override():
        yield AsyncMock()

    @staticmethod
    def _extract_csrf_token(html: str) -> str:
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if match is None:
            raise AssertionError("csrf_token input not found in settings page")
        return match.group(1)

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

        @app.get("/caddyfile", name="caddyfile_page")
        async def _caddyfile_page() -> dict[str, str]:
            return {"status": "ok"}

        @app.post("/logout", name="logout_action")
        async def _logout_action() -> dict[str, str]:
            return {"status": "ok"}

        app.include_router(settings_router)
        app.dependency_overrides[get_db_session] = self._session_override
        return app

    def test_settings_page_renders_caddy_configuration_values(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.settings.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.settings.get_caddy_config",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        admin_url="http://localhost:2019",
                        caddyfile_path_str="/app/Caddyfile",
                    )
                ),
            ),
            patch("app.routers.ui.settings.get_rate_limit_enabled", new=AsyncMock(return_value=True)),
            patch("app.routers.ui.settings.get_ssllabs_email", new=AsyncMock(return_value="team@example.com")),
        ):
            with TestClient(app) as client:
                response = client.get("/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Save Settings", response.text)
        self.assertIn('value="http://localhost:2019"', response.text)
        self.assertIn('value="/app/Caddyfile"', response.text)
        self.assertIn('id="rate_limit_enabled"', response.text)
        self.assertIn('value="team@example.com"', response.text)
        self.assertIn("Save SSL Labs Email", response.text)
        self.assertIn("data-auto-save-form", response.text)
        self.assertIn("data-auto-save-field", response.text)
        self.assertIn("data-auto-save-status", response.text)

    def test_settings_page_updates_caddy_configuration(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.settings.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.settings.get_caddy_config",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        admin_url="http://localhost:2019",
                        caddyfile_path_str="/app/Caddyfile",
                    )
                ),
            ),
            patch("app.routers.ui.settings.get_rate_limit_enabled", new=AsyncMock(return_value=True)),
            patch("app.routers.ui.settings.get_ssllabs_email", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.settings.set_caddy_config", new=AsyncMock()) as set_caddy_config,
            patch("app.routers.ui.settings.set_rate_limit_enabled", new=AsyncMock()) as set_rate_limit,
            patch("app.routers.ui.settings.update_rate_limit_enabled"),
        ):
            with TestClient(app) as client:
                page = client.get("/settings")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/settings/caddy",
                    data={
                        "csrf_token": csrf_token,
                        "caddy_api_url": "http://host.docker.internal:2019",
                        "caddyfile_path": "/etc/caddy/Caddyfile",
                        "rate_limit_enabled": "on",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/settings")
        set_caddy_config.assert_awaited_once()
        set_rate_limit.assert_awaited_once_with(ANY, True)

    def test_settings_page_updates_caddy_configuration_via_json(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.settings.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.settings.get_caddy_config",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        admin_url="http://localhost:2019",
                        caddyfile_path_str="/app/Caddyfile",
                    )
                ),
            ),
            patch("app.routers.ui.settings.get_rate_limit_enabled", new=AsyncMock(return_value=True)),
            patch("app.routers.ui.settings.get_ssllabs_email", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.settings.set_caddy_config", new=AsyncMock()) as set_caddy_config,
            patch("app.routers.ui.settings.set_rate_limit_enabled", new=AsyncMock()) as set_rate_limit,
            patch("app.routers.ui.settings.update_rate_limit_enabled"),
        ):
            with TestClient(app) as client:
                page = client.get("/settings")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/settings/caddy",
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    data={
                        "csrf_token": csrf_token,
                        "caddy_api_url": "http://host.docker.internal:2019",
                        "caddyfile_path": "/etc/caddy/Caddyfile",
                        "rate_limit_enabled": "on",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True, "message": "Settings updated."})
        set_caddy_config.assert_awaited_once()
        set_rate_limit.assert_awaited_once_with(ANY, True)

    def test_settings_page_updates_ssllabs_email(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.settings.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.settings.get_caddy_config",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        admin_url="http://localhost:2019",
                        caddyfile_path_str="/app/Caddyfile",
                    )
                ),
            ),
            patch("app.routers.ui.settings.get_rate_limit_enabled", new=AsyncMock(return_value=True)),
            patch("app.routers.ui.settings.get_ssllabs_email", new=AsyncMock(side_effect=[None, None])) as get_ssllabs_email,
            patch("app.routers.ui.settings.set_ssllabs_email", new=AsyncMock()) as set_ssllabs_email,
            patch("app.routers.ui.settings.clear_registration_status_cache") as clear_cache,
        ):
            with TestClient(app) as client:
                page = client.get("/settings")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/settings/ssllabs",
                    data={
                        "csrf_token": csrf_token,
                        "ssllabs_email": "team@example.com",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/settings")
        self.assertEqual(get_ssllabs_email.await_count, 2)
        set_ssllabs_email.assert_awaited_once_with(ANY, "team@example.com")
        clear_cache.assert_called_once_with("team@example.com")

    def test_settings_page_updates_ssllabs_email_via_json(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.settings.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.settings.get_caddy_config",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        admin_url="http://localhost:2019",
                        caddyfile_path_str="/app/Caddyfile",
                    )
                ),
            ),
            patch("app.routers.ui.settings.get_rate_limit_enabled", new=AsyncMock(return_value=True)),
            patch("app.routers.ui.settings.get_ssllabs_email", new=AsyncMock(side_effect=[None, None])),
            patch("app.routers.ui.settings.set_ssllabs_email", new=AsyncMock()) as set_ssllabs_email,
            patch("app.routers.ui.settings.clear_registration_status_cache") as clear_cache,
        ):
            with TestClient(app) as client:
                page = client.get("/settings")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/settings/ssllabs",
                    headers={
                        "Accept": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    data={
                        "csrf_token": csrf_token,
                        "ssllabs_email": "team@example.com",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True, "message": "SSL Labs email updated."})
        set_ssllabs_email.assert_awaited_once_with(ANY, "team@example.com")
        clear_cache.assert_called_once_with("team@example.com")