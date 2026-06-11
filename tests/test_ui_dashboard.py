#!/usr/bin/env python3
#
# tests/test_ui_dashboard.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config.settings import get_settings


_ENV_OVERRIDES = {
    "CB_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CB_ADMIN_PASSWORD": "UnitTestPassword-123A",
    "CADDYBUDDY_ADMIN_PASSWORD": "UnitTestPassword-123A",
}
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}

for key, value in _ENV_OVERRIDES.items():
    os.environ[key] = value

get_settings.cache_clear()

from fastapi.testclient import TestClient
from app.routers.ui.auth import router as auth_router
from app.routers.ui.dashboard import router as dashboard_router
from tests.ui_test_app import build_ui_test_app


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


class UIDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        for key, value in _ENV_OVERRIDES.items():
            os.environ[key] = value
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()

    @staticmethod
    async def _session_override():
        yield AsyncMock()

    def _build_app(self):
        return build_ui_test_app(
            dashboard_router,
            session_override=self._session_override,
            stub_routes=[
                ("GET", "/caddyfile", "caddyfile_page"),
                ("GET", "/sites", "sites_page"),
                ("POST", "/logout", "logout_action"),
            ],
        )

    def test_home_page_renders_dashboard_metrics(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        metrics = SimpleNamespace(
            domain_count=12,
            enabled_domain_count=10,
            valid_certificate_count=5,
            expired_certificate_count=2,
            expiring_soon_certificate_count=1,
            caddy_service_status="Running",
            caddy_service_uptime="2h 15m",
            caddy_version="v2.8.4",
        )

        with (
            patch("app.routers.ui.dashboard.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.dashboard.get_dashboard_metrics", new=AsyncMock(return_value=metrics)),
            patch(
                "app.routers.ui.dashboard.get_caddy_runtime_status",
                new=AsyncMock(return_value=SimpleNamespace(onboarding_required=False)),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Dashboard", response.text)
        self.assertIn("Managed sites, certificate state and local Caddy runtime status.", response.text)
        self.assertNotIn("Enabled / total site domains tracked by CaddyBuddy.", response.text)
        self.assertNotIn("Local Caddy certificates that are still valid.", response.text)
        self.assertNotIn("Local Caddy certificates that are already expired.", response.text)
        self.assertIn('/static/js/app-status.js', response.text)
        self.assertNotIn('/static/js/app.js', response.text)
        self.assertIn('data-dashboard-metrics-url="/api/v1/dashboard/metrics"', response.text)
        self.assertEqual(response.text.count('class="col-6 col-md-4"'), 3)
        self.assertEqual(response.text.count('class="hero-metric__icon" aria-hidden="true"'), 3)
        self.assertIn(">10<", response.text)  # enabled_domain_count
        self.assertIn("/12</span>", response.text)  # total domain_count
        self.assertIn(">5<", response.text)
        self.assertIn(">2<", response.text)
        self.assertIn("Caddy", response.text)
        self.assertIn("Uptime 2h 15m", response.text)
        self.assertIn("id=\"caddy-version\">v2.8.4</span>", response.text)
        self.assertIn("Certificate warning.", response.text)
        self.assertIn("1 certificate expire within 7 days.", response.text)
        self.assertIn("data-ui-lint-dynamic", response.text)
        self.assertNotIn("(APP_VER)", response.text)
        self.assertNotIn('col-12 col-md-4', response.text)
        self.assertNotIn("Create your first managed site", response.text)

    def test_home_page_renders_empty_state_when_no_domains_exist(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        metrics = SimpleNamespace(
            domain_count=0,
            enabled_domain_count=0,
            valid_certificate_count=0,
            expired_certificate_count=0,
            expiring_soon_certificate_count=0,
            caddy_service_status="Running",
            caddy_service_uptime="Unavailable",
            caddy_version="v2.8.4",
        )

        with (
            patch("app.routers.ui.dashboard.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.dashboard.get_dashboard_metrics", new=AsyncMock(return_value=metrics)),
            patch(
                "app.routers.ui.dashboard.get_caddy_runtime_status",
                new=AsyncMock(return_value=SimpleNamespace(onboarding_required=False)),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Create your first managed site", response.text)
        self.assertIn("Add a domain and deploy its Caddy configuration from CaddyBuddy.", response.text)
        self.assertIn('href="/sites"', response.text)
        self.assertIn('id="dashboard-certificate-warning"', response.text)
        self.assertIn('d-none', response.text)

    def test_home_page_points_to_caddyfile_when_onboarding_is_required(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        metrics = SimpleNamespace(
            domain_count=0,
            enabled_domain_count=0,
            valid_certificate_count=0,
            expired_certificate_count=0,
            expiring_soon_certificate_count=0,
            caddy_service_status="Running",
            caddy_service_uptime="Unavailable",
            caddy_version="v2.8.4",
        )

        with (
            patch("app.routers.ui.dashboard.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.dashboard.get_dashboard_metrics", new=AsyncMock(return_value=metrics)),
            patch(
                "app.routers.ui.dashboard.get_caddy_runtime_status",
                new=AsyncMock(return_value=SimpleNamespace(onboarding_required=True)),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Create the managed Caddyfile first", response.text)
        self.assertIn(
            "Import the host-mounted Caddyfile into CaddyBuddy and replace it with the managed marker before creating",
            response.text,
        )
        self.assertIn('href="/caddyfile"', response.text)
        self.assertNotIn("href=\"/sites\" class=\"btn btn-primary\">Create site", response.text)

    def test_home_page_redirects_anonymous_user_to_login(self) -> None:
        app = self._build_app()

        with patch("app.routers.ui.dashboard.require_user", new=AsyncMock(return_value=None)):
            with TestClient(app) as client:
                response = client.get("/", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_home_page_redirects_anonymous_user_without_sign_in_toast(self) -> None:
        app = self._build_app()
        app.include_router(auth_router)

        with TestClient(app) as client:
            response = client.get("/", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Please sign in to continue", response.text)


if __name__ == "__main__":
    unittest.main()