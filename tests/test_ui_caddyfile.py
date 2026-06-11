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
from app.routers.ui.caddyfile import router as caddyfile_router
from tests.ui_test_app import build_ui_test_app


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

    def _build_app(self):
        return build_ui_test_app(
            caddyfile_router,
            session_override=self._session_override,
            stub_routes=[
                ("GET", "/", "home_page"),
                ("GET", "/sites", "sites_page"),
                ("POST", "/logout", "logout_action"),
            ],
        )

    def test_caddyfile_page_disables_validate_button_when_empty(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        runtime_status = SimpleNamespace(
            onboarding_required=False,
            caddyfile_path="/app/Caddyfile",
            admin_api_reachable=True,
        )

        with (
            patch("app.routers.ui.caddyfile.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.caddyfile.get_baseline_caddyfile", new=AsyncMock(return_value="")),
            patch("app.routers.ui.caddyfile.get_caddy_runtime_status", new=AsyncMock(return_value=runtime_status)),
        ):
            with TestClient(app) as client:
                response = client.get("/caddyfile")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="app-page app-page--caddyfile"', response.text)
        self.assertIn('class="panel-card caddyfile-editor-panel"', response.text)
        self.assertIn('class="d-grid gap-3 caddyfile-editor-panel__form"', response.text)
        self.assertIn('class="form-control font-monospace caddyfile-editor-panel__textarea"', response.text)
        self.assertIn("data-caddyfile-config-form", response.text)
        self.assertIn('id="caddyfile-validate-btn"', response.text)
        self.assertIn('data-validate-error-prefix="Caddyfile invalid"', response.text)
        self.assertRegex(
            response.text,
            r'id="caddyfile-validate-btn"[^>]*disabled[^>]*aria-disabled="true"',
        )
        self.assertIn('build ', response.text)

    def test_caddyfile_page_buttons_start_disabled_until_changes(self) -> None:
        """Buttons start disabled; JavaScript enables them when changes are made."""
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        runtime_status = SimpleNamespace(
            onboarding_required=False,
            caddyfile_path="/app/Caddyfile",
            admin_api_reachable=True,
        )

        with (
            patch("app.routers.ui.caddyfile.require_user", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.caddyfile.get_baseline_caddyfile",
                new=AsyncMock(return_value="{")
            ),
            patch("app.routers.ui.caddyfile.get_caddy_runtime_status", new=AsyncMock(return_value=runtime_status)),
        ):
            with TestClient(app) as client:
                response = client.get("/caddyfile")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="app-page app-page--caddyfile"', response.text)
        self.assertIn('class="panel-card caddyfile-editor-panel"', response.text)
        self.assertIn('id="caddyfile-validate-btn"', response.text)
        self.assertIn('id="caddyfile-save-btn"', response.text)
        # Buttons start disabled in HTML; JavaScript enables them on change
        self.assertRegex(
            response.text,
            r'id="caddyfile-validate-btn"[^>]*disabled[^>]*aria-disabled="true"',
        )
        self.assertRegex(
            response.text,
            r'id="caddyfile-save-btn"[^>]*disabled[^>]*aria-disabled="true"',
        )
        self.assertIn('build ', response.text)

    def test_caddyfile_page_shows_configured_mounted_path_in_onboarding_notice(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        runtime_status = SimpleNamespace(
            onboarding_required=True,
            caddyfile_path="/custom/mounted/Caddyfile",
            admin_api_reachable=True,
        )

        with (
            patch("app.routers.ui.caddyfile.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.caddyfile.get_baseline_caddyfile", new=AsyncMock(return_value="")),
            patch("app.routers.ui.caddyfile.get_caddy_runtime_status", new=AsyncMock(return_value=runtime_status)),
        ):
            with TestClient(app) as client:
                response = client.get("/caddyfile")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Mounted Caddyfile Path", response.text)
        self.assertIn("/custom/mounted/Caddyfile", response.text)


if __name__ == "__main__":
    unittest.main()