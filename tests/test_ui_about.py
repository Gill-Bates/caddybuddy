#!/usr/bin/env python3
#
# tests/test_ui_about.py
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
from app.routers.ui.about import router as about_router
from tests.ui_test_app import build_ui_test_app


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


class UIAboutTests(unittest.TestCase):
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

    def _build_app(self):
        return build_ui_test_app(
            about_router,
            session_override=self._session_override,
            stub_routes=[
                ("GET", "/", "home_page"),
                ("GET", "/sites", "sites_page"),
                ("GET", "/caddyfile", "caddyfile_page"),
                ("POST", "/logout", "logout_action"),
            ],
        )

    def test_about_page_renders_sections(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        with patch("app.routers.ui.about.require_user", new=AsyncMock(return_value=current_user)):
            with TestClient(app) as client:
                response = client.get("/about")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Application Details", response.text)
        self.assertIn("Check for Updates", response.text)
        self.assertIn("Dependencies", response.text)
        self.assertIn("Changelog", response.text)
        self.assertIn('href="https://gill-bates.github.io/caddybuddy/"', response.text)
        # A known dependency and the update-check widget should be present.
        self.assertIn("fastapi", response.text)
        self.assertIn('id="btn-check-updates"', response.text)

    def test_about_page_redirects_when_anonymous(self) -> None:
        app = self._build_app()
        with patch("app.routers.ui.about.require_user", new=AsyncMock(return_value=None)):
            with TestClient(app) as client:
                response = client.get("/about", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_check_updates_returns_json(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        payload = {
            "update_available": True,
            "current_version": "1.5",
            "latest_version": "1.6",
            "release_url": "https://github.com/Gill-Bates/caddybuddy/releases/tag/v1.6",
            "published_at": "2026-07-01T00:00:00Z",
            "error": None,
        }
        with (
            patch("app.routers.ui.about.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.about.check_for_updates", new=AsyncMock(return_value=payload)) as mock_check,
        ):
            with TestClient(app) as client:
                response = client.get("/about/check-updates?force=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        # Admin may force a live check.
        mock_check.assert_awaited_once_with(True)

    def test_check_updates_force_restricted_to_admin(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="bob", role="user")
        with (
            patch("app.routers.ui.about.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.about.check_for_updates", new=AsyncMock(return_value={})) as mock_check,
        ):
            with TestClient(app) as client:
                response = client.get("/about/check-updates?force=true")

        self.assertEqual(response.status_code, 200)
        # Non-admin force requests are downgraded to a cached check.
        mock_check.assert_awaited_once_with(False)

    def test_check_updates_requires_authentication(self) -> None:
        app = self._build_app()
        with patch("app.routers.ui.about.require_user", new=AsyncMock(return_value=None)):
            with TestClient(app) as client:
                response = client.get("/about/check-updates")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
