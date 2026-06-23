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
from app.config.limiter import limiter


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
from app.routers.ui.settings import router as settings_router
from tests.ui_test_app import build_ui_test_app


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
        self.onboarding_patcher = patch(
            "app.routers.ui._common.get_onboarding_state",
            new=AsyncMock(return_value=SimpleNamespace(status="completed")),
        )
        self.onboarding_patcher.start()

    def tearDown(self) -> None:
        self.onboarding_patcher.stop()
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

    def _build_app(self):
        return build_ui_test_app(
            settings_router,
            session_override=self._session_override,
            stub_routes=[
                ("GET", "/", "home_page"),
                ("GET", "/sites", "sites_page"),
                ("GET", "/caddyfile", "caddyfile_page"),
                ("POST", "/logout", "logout_action"),
            ],
        )

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
            patch(
                "app.routers.ui.settings.check_email_registration_status",
                new=AsyncMock(return_value=True),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn('value="http://localhost:2019"', response.text)
        self.assertIn('value="/app/Caddyfile"', response.text)
        self.assertIn('id="rate_limit_enabled"', response.text)
        self.assertIn('id="rate_limit_enabled" name="rate_limit_enabled" checked data-auto-save-field', response.text)
        self.assertIn('value="team@example.com"', response.text)
        self.assertIn('data-ssllabs-preloaded="true"', response.text)
        self.assertIn('id="ssllabs-register-btn"', response.text)
        self.assertIn('d-none', response.text)
        self.assertIn("Global Settings", response.text)
        self.assertIn("Restart onboarding wizard", response.text)
        self.assertIn("SSL Labs API", response.text)
        self.assertIn("SSL Labs History Retention", response.text)
        self.assertIn("Change Password", response.text)
        self.assertLess(response.text.index("Change Password"), response.text.index("Global Settings"))
        self.assertLess(response.text.index("Global Settings"), response.text.index("SSL Labs API"))
        self.assertLess(response.text.index("SSL Labs API"), response.text.index("SSL Labs History Retention"))
        self.assertIn("data-auto-save-form", response.text)
        self.assertIn("data-auto-save-field", response.text)
        self.assertIn('data-require-csrf', response.text)
        self.assertIn('data-password-policy-min-length="8"', response.text)
        self.assertIn('data-password-policy-message="Password must be at least 8 characters long and contain uppercase, lowercase, digit, and special character."', response.text)
        self.assertIn("data-password-checklist-form", response.text)
        self.assertIn("data-password-checklist-password", response.text)
        self.assertIn("data-password-checklist-confirm", response.text)
        self.assertIn('class="setup-checklist mb-4"', response.text)
        self.assertIn('data-check="match"', response.text)
        self.assertNotIn("Save Settings", response.text)
        self.assertNotIn('data-auto-save-status', response.text)
        self.assertIn('minlength="8"', response.text)
        self.assertIn("Password must be at least 8 characters long and contain uppercase, lowercase, digit, and special character.", response.text)
        self.assertIn('class="row app-grid settings-layout"', response.text)
        self.assertIn('class="col-12 col-xl-6 settings-column settings-column--primary"', response.text)
        self.assertIn('class="col-12 col-xl-6 settings-column settings-column--secondary"', response.text)
        self.assertIn('class="col-12 settings-column settings-column--retention"', response.text)
        self.assertIn('class="panel-card settings-panel settings-panel--primary"', response.text)
        self.assertIn('class="row g-4 settings-stack"', response.text)
        self.assertIn('id="ssllabs-retention-settings"', response.text)
        self.assertIn("How long SSL Labs grade history samples are kept for the dashboard chart.", response.text)
        self.assertNotIn("How long daily SSL Labs grade history is kept for the dashboard chart.", response.text)
        self.assertIn('class="ssllabs-retention-scale"', response.text, "Retention slider must have scale wrapper for tick labels")
        self.assertIn('class="ssllabs-retention-labels"', response.text, "Retention slider must have tick label container")
        self.assertIn('class="ssllabs-retention-label"', response.text, "Retention slider must have individual tick labels")
        self.assertIn('value="3"', response.text)
        self.assertEqual(response.text.count('class="ssllabs-retention-label"'), 4)
        self.assertIn('class="badge cb-pill text-bg-secondary" id="ssllabs-retention-badge"', response.text)
        # Tick labels must use human-readable names matching SSLLABS_RETENTION_DAY_VALUES
        self.assertIn("30d", response.text, "Tick label for 30 days must render as '30d'")
        self.assertIn("90d", response.text, "Tick label for 90 days must render as '90d'")
        self.assertIn("180d", response.text, "Tick label for 180 days must render as '180d'")
        self.assertIn("1y", response.text, "Tick label for 365 days must render as '1y'")
        self.assertNotIn("365d", response.text, "365 days must not appear as '365d' — use '1y'")
        # Slider range must span exactly 0 … len(values)-1 to match the label grid
        self.assertIn('min="0" max="3"', response.text, "Slider range must cover 4 steps (0-3) for the 4 retention values")
        # data-retention-values must expose the full allowed set for the JS formatLabel
        self.assertIn('data-retention-values="[30, 90, 180, 365]"', response.text, "data-retention-values must list all allowed retention day counts")

    def test_settings_page_restarts_onboarding_wizard(self) -> None:
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
            patch("app.routers.ui.settings.reset_onboarding_state", new=AsyncMock()) as reset_onboarding_state,
        ):
            with TestClient(app) as client:
                page = client.get("/settings")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/settings/onboarding/restart",
                    data={"csrf_token": csrf_token},
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/onboarding")
        reset_onboarding_state.assert_awaited_once()

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
        set_caddy_config.assert_awaited_once_with(
            ANY,
            api_url="http://host.docker.internal:2019",
            caddyfile_path="/etc/caddy/Caddyfile",
        )
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
        set_caddy_config.assert_awaited_once_with(
            ANY,
            api_url="http://host.docker.internal:2019",
            caddyfile_path="/etc/caddy/Caddyfile",
        )
        set_rate_limit.assert_awaited_once_with(ANY, True)

    def test_settings_page_updates_caddy_configuration_when_enabling_rate_limit(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        original_rate_limit_enabled = limiter.enabled

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
            patch("app.routers.ui.settings.get_rate_limit_enabled", new=AsyncMock(return_value=False)),
            patch("app.routers.ui.settings.get_ssllabs_email", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.settings.set_caddy_config", new=AsyncMock()) as set_caddy_config,
            patch("app.routers.ui.settings.set_rate_limit_enabled", new=AsyncMock()) as set_rate_limit,
            patch("app.routers.ui.settings.update_rate_limit_enabled") as update_rate_limit,
        ):
            limiter.enabled = False
            try:
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
            finally:
                limiter.enabled = original_rate_limit_enabled

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/settings")
        set_caddy_config.assert_awaited_once_with(
            ANY,
            api_url="http://host.docker.internal:2019",
            caddyfile_path="/etc/caddy/Caddyfile",
        )
        set_rate_limit.assert_awaited_once_with(ANY, True)
        update_rate_limit.assert_called_once_with(True)

    def test_settings_page_disables_rate_limit_when_checkbox_is_off(self) -> None:
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
            patch("app.routers.ui.settings.update_rate_limit_enabled") as update_rate_limit,
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
                    },
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/settings")
        set_caddy_config.assert_awaited_once_with(
            ANY,
            api_url="http://host.docker.internal:2019",
            caddyfile_path="/etc/caddy/Caddyfile",
        )
        set_rate_limit.assert_awaited_once_with(ANY, False)
        update_rate_limit.assert_called_once_with(False)

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
            patch("app.routers.ui.settings.get_ssllabs_email", new=AsyncMock(return_value=None)) as get_ssllabs_email,
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
        self.assertGreaterEqual(get_ssllabs_email.await_count, 1)
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
            patch("app.routers.ui.settings.get_ssllabs_email", new=AsyncMock(return_value=None)),
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

    def test_settings_page_updates_ssllabs_email_starts_scheduler(self) -> None:
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
            patch("app.routers.ui.settings.set_ssllabs_email", new=AsyncMock()),
            patch("app.routers.ui.settings.clear_registration_status_cache"),
            patch("app.routers.ui.settings.ssllabs_service.startup", new=AsyncMock()) as startup,
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
        startup.assert_awaited_once()

    def test_settings_updates_ssllabs_retention(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.settings.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.settings.get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019", caddyfile_path_str="/app/Caddyfile")),
            ),
            patch("app.routers.ui.settings.get_rate_limit_enabled", new=AsyncMock(return_value=True)),
            patch("app.routers.ui.settings.get_ssllabs_email", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.settings.get_ssllabs_history_retention_days", new=AsyncMock(return_value=365)),
            patch("app.routers.ui.settings.set_ssllabs_history_retention_days", new=AsyncMock()) as set_retention,
        ):
            with TestClient(app) as client:
                page = client.get("/settings")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/settings/ssllabs-retention",
                    data={"csrf_token": csrf_token, "retention_days": "90"},
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/settings")
        set_retention.assert_awaited_once_with(ANY, 90)

    def test_settings_rejects_non_numeric_retention(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.settings.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.settings.get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019", caddyfile_path_str="/app/Caddyfile")),
            ),
            patch("app.routers.ui.settings.get_rate_limit_enabled", new=AsyncMock(return_value=True)),
            patch("app.routers.ui.settings.get_ssllabs_email", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.settings.get_ssllabs_history_retention_days", new=AsyncMock(return_value=365)),
            patch("app.routers.ui.settings.set_ssllabs_history_retention_days", new=AsyncMock()) as set_retention,
        ):
            with TestClient(app) as client:
                page = client.get("/settings")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/settings/ssllabs-retention",
                    data={"csrf_token": csrf_token, "retention_days": "abc"},
                    headers={"Accept": "application/json"},
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 400)
        set_retention.assert_not_awaited()

    def test_change_password_reinitializes_current_session(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(id=7, username="admin", role="admin", password_hash="old-hash")

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
            patch("app.routers.ui.settings.auth_service.verify_password", new=AsyncMock(return_value=True)),
            patch("app.routers.ui.settings.auth_service.hash_password", new=AsyncMock(return_value="new-hash")),
            patch("app.routers.ui.settings.user_repository.update_password", new=AsyncMock()) as update_password,
            patch("app.routers.ui.settings.initialize_user_session") as initialize_session,
        ):
            with TestClient(app) as client:
                page = client.get("/settings")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/settings/change-password",
                    data={
                        "csrf_token": csrf_token,
                        "current_password": "OldPassword123!",
                        "new_password": "NewPassword123!",
                        "confirm_password": "NewPassword123!",
                    },
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/settings")
        update_password.assert_awaited_once_with(ANY, current_user, "new-hash")
        initialize_session.assert_called_once_with(unittest.mock.ANY, 7, "new-hash")
