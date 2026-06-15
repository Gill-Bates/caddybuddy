#!/usr/bin/env python3
#
# tests/test_ui_auth.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config.settings import get_settings
from app.services.auth import PASSWORD_MIN_LENGTH


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
from tests.ui_test_app import build_ui_test_app


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

    def _build_app(self):
        return build_ui_test_app(
            auth_router,
            session_override=self._session_override,
            stub_routes=[],
        )

    @staticmethod
    def _extract_csrf_token(html: str) -> str:
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if match is None:
            raise AssertionError("csrf_token input not found in login page")
        return match.group(1)

    @staticmethod
    async def _session_override():
        yield AsyncMock()

    def test_global_css_keeps_touch_targets_at_least_44px(self) -> None:
        css_path = Path(__file__).resolve().parents[1] / "app/static/css/app.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn(
            ".btn {\n    display: inline-flex;\n    align-items: center;\n    justify-content: center;\n    min-block-size: 2.75rem;",
            css,
            "Buttons must keep a 44px minimum touch target.",
        )
        self.assertIn(
            ".form-select {\n    min-block-size: 2.75rem;\n}",
            css,
            "Select controls must keep a 44px minimum touch target.",
        )
        self.assertIn(
            ".cb-footer-link {\n    color: inherit;\n    transition: color 0.15s ease-in-out;\n    display: inline-flex;\n    flex: 0 0 auto;\n    align-items: center;\n    justify-content: center;\n    gap: 0.3rem;\n    min-width: 2.75rem;\n    min-height: 2.75rem;",
            css,
            "Footer links must keep a 44px minimum touch target.",
        )

    def test_login_failure_returns_403_and_logs_single_warning(self) -> None:
        app = self._build_app()

        with (
            patch("app.routers.ui.auth.auth_service.authenticate", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.auth.logger.warning") as warning,
        ):
            with TestClient(app) as client:
                login_page = client.get("/login")
                self.assertNotIn('placeholder="Enter username"', login_page.text)
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
        self.assertIn('class="toast-container app-toast-stack position-fixed top-0 end-0 p-3"', response.text)
        self.assertIn('class="toast align-items-center text-bg-danger border-0 shadow-sm"', response.text)
        self.assertIn('role="alert"', response.text)
        self.assertIn('aria-live="assertive"', response.text)
        self.assertIn('data-auto-dismiss-delay="12000"', response.text)
        self.assertIn('class="toast-body">Invalid credentials.</div>', response.text)
        self.assertIn('<div class="alert alert-danger login-error" role="alert" data-testid="login-error">Invalid credentials.</div>', response.text)
        warning.assert_called_once_with("Authentication failed for username=%r status_code=403", "admin")

    def test_successful_login_keeps_redirect_flow(self) -> None:
        app = self._build_app()
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

    def test_logout_redirects_without_sign_out_toast(self) -> None:
        app = self._build_app()
        user = SimpleNamespace(id=1, username="admin")

        with TestClient(app) as client:
            login_page = client.get("/login")
            csrf_token = self._extract_csrf_token(login_page.text)
            with patch("app.routers.ui.auth.get_session_user", new=AsyncMock(return_value=user)):
                response = client.post(
                    "/logout",
                    data={"csrf_token": csrf_token},
                    follow_redirects=False,
                )
            follow_up = client.get("/login")

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")
        self.assertEqual(follow_up.status_code, 200)
        self.assertIn("Sign in", follow_up.text)
        self.assertNotIn("You have been signed out.", follow_up.text)
        self.assertNotIn('class="toast-container app-toast-stack', follow_up.text)

    def test_login_page_shows_setup_form_when_no_users_exist(self) -> None:
        app = self._build_app()

        with patch("app.routers.ui.auth.user_repository.exists_any", new=AsyncMock(return_value=False)):
            with TestClient(app) as client:
                response = client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Create your admin account to get started", response.text)
        self.assertIn("data-setup-form", response.text)
        self.assertIn('name="confirm_password"', response.text)
        self.assertNotIn('id="setup-submit" disabled', response.text)
        self.assertIn(f'minlength="{PASSWORD_MIN_LENGTH}"', response.text)
        self.assertIn(f'data-setup-password-min-length="{PASSWORD_MIN_LENGTH}"', response.text)
        self.assertIn(f"At least {PASSWORD_MIN_LENGTH} characters", response.text)
        self.assertNotIn('name="username"', response.text)
        self.assertNotIn('placeholder="Enter username"', response.text)

    def test_setup_creates_admin_and_redirects_to_onboarding(self) -> None:
        app = self._build_app()
        created_user = SimpleNamespace(id=1, username="admin", password_hash="hashed-pw")

        with (
            patch("app.routers.ui.auth.user_repository.exists_any", new=AsyncMock(return_value=False)),
            patch("app.routers.ui.auth.auth_service.ensure_default_admin", new=AsyncMock(return_value=created_user)),
            patch("app.routers.ui.auth.initialize_user_session") as init_session,
        ):
            with TestClient(app) as client:
                login_page = client.get("/login")
                csrf_token = self._extract_csrf_token(login_page.text)
                response = client.post(
                    "/setup",
                    data={
                        "password": "StrongAdmin1!",
                        "confirm_password": "StrongAdmin1!",
                        "csrf_token": csrf_token,
                    },
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/onboarding")
        init_session.assert_called_once_with(unittest.mock.ANY, 1, "hashed-pw")

    def test_setup_redirects_to_login_when_admin_creation_returns_none(self) -> None:
        app = self._build_app()

        with (
            patch("app.routers.ui.auth.user_repository.exists_any", new=AsyncMock(return_value=False)),
            patch("app.routers.ui.auth.auth_service.ensure_default_admin", new=AsyncMock(return_value=None)),
        ):
            with TestClient(app) as client:
                login_page = client.get("/login")
                csrf_token = self._extract_csrf_token(login_page.text)
                response = client.post(
                    "/setup",
                    data={
                        "password": "StrongAdmin1!",
                        "confirm_password": "StrongAdmin1!",
                        "csrf_token": csrf_token,
                    },
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_setup_redirects_to_login_when_users_already_exist(self) -> None:
        app = self._build_app()

        with patch("app.routers.ui.auth.user_repository.exists_any", new=AsyncMock(side_effect=[True, True])):
            with TestClient(app) as client:
                login_page = client.get("/login")
                csrf_token = self._extract_csrf_token(login_page.text)
                response = client.post(
                    "/setup",
                    data={
                        "password": "StrongAdmin1!",
                        "confirm_password": "StrongAdmin1!",
                        "csrf_token": csrf_token,
                    },
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_setup_returns_422_for_password_mismatch(self) -> None:
        app = self._build_app()

        with patch("app.routers.ui.auth.user_repository.exists_any", new=AsyncMock(return_value=False)):
            with TestClient(app) as client:
                login_page = client.get("/login")
                csrf_token = self._extract_csrf_token(login_page.text)
                response = client.post(
                    "/setup",
                    data={
                        "password": "StrongAdmin1!",
                        "confirm_password": "DifferentPw2@",
                        "csrf_token": csrf_token,
                    },
                )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Passwords do not match", response.text)
        self.assertIn("data-setup-form", response.text)

    def test_setup_returns_422_for_weak_password(self) -> None:
        app = self._build_app()

        with patch("app.routers.ui.auth.user_repository.exists_any", new=AsyncMock(return_value=False)):
            with TestClient(app) as client:
                login_page = client.get("/login")
                csrf_token = self._extract_csrf_token(login_page.text)
                response = client.post(
                    "/setup",
                    data={
                        "password": "weakpassword",
                        "confirm_password": "weakpassword",
                        "csrf_token": csrf_token,
                    },
                )

        self.assertEqual(response.status_code, 422)
        self.assertIn("data-setup-form", response.text)


if __name__ == "__main__":
    unittest.main()
