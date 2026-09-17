#!/usr/bin/env python3
#
# tests/test_ui_auth.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config.limiter import limiter
from app.config.settings import get_settings
from app.services.auth import PASSWORD_MIN_LENGTH
from app.utils.hidden_captcha import CaptchaOutcome
from tests.env_overrides import ModuleEnv

_ENV = ModuleEnv()

from fastapi.testclient import TestClient

from app.routers.ui.auth import router as auth_router
from tests.ui_test_app import build_ui_test_app, extract_csrf_token


def tearDownModule() -> None:
    _ENV.restore()


class UIAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        _ENV.apply()
        self.captcha_verifier_patcher = patch(
            "app.routers.ui.auth.verify_captcha_token",
            return_value=CaptchaOutcome.OK,
        )
        self.captcha_verifier = self.captcha_verifier_patcher.start()
        # The login page asks whether any passkey exists; the mocked session
        # cannot answer that, so tests opt in explicitly where it matters.
        self.passkey_available_patcher = patch(
            "app.routers.ui.auth.passkey_service.any_registered",
            new=AsyncMock(return_value=False),
        )
        self.passkey_available = self.passkey_available_patcher.start()
        # Likewise for "does any user exist" (setup mode); setup-mode tests patch it to False.
        self.users_exist_patcher = patch(
            "app.routers.ui.auth.user_repository.exists_any",
            new=AsyncMock(return_value=True),
        )
        self.users_exist_patcher.start()
        self._limiter_enabled = limiter.enabled
        limiter.enabled = False

    def tearDown(self) -> None:
        self.captcha_verifier_patcher.stop()
        self.passkey_available_patcher.stop()
        self.users_exist_patcher.stop()
        limiter.enabled = self._limiter_enabled
        get_settings.cache_clear()

    def _build_app(self):
        return build_ui_test_app(
            auth_router,
            session_override=self._session_override,
            stub_routes=[],
        )

    @staticmethod
    def _extract_captcha_token(html: str) -> str:
        match = re.search(r'name="captcha_token" value="([^"]+)"', html)
        if match is None:
            raise AssertionError("captcha_token input not found in login page")
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
            ".cb-footer-link {\n    color: inherit;\n    transition: color 0.15s ease-in-out;\n    display: inline-flex;\n    flex: 0 0 auto;\n    align-items: center;\n    justify-content: center;\n    gap: 0.2rem;\n    min-width: 0;\n    min-height: 2.75rem;",
            css,
            "Footer links must keep a 44px minimum touch target.",
        )

    def test_login_failure_returns_403_and_logs_single_warning(self) -> None:
        app = self._build_app()

        with (
            patch("app.routers.ui.auth.auth_service.authenticate", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.auth.log_authentication_failure") as log_failure,
            TestClient(app) as client,
        ):
            login_page = client.get("/login")
            self.assertNotIn('placeholder="Enter username"', login_page.text)
            csrf_token = extract_csrf_token(login_page.text)
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
        self.assertIn('class="toast-container app-toast-stack position-fixed bottom-0 end-0 p-3"', response.text)
        self.assertIn('class="toast toast-slide align-items-center text-bg-danger border-0"', response.text)
        self.assertIn('role="alert"', response.text)
        self.assertIn('aria-live="assertive"', response.text)
        self.assertIn('data-auto-dismiss-delay="12000"', response.text)
        self.assertIn('class="toast-body">Invalid credentials.</div>', response.text)
        self.assertIn('<div class="alert alert-danger login-error" role="alert" data-testid="login-error">Invalid credentials.</div>', response.text)
        log_failure.assert_called_once_with(
            unittest.mock.ANY,
            username="admin",
            reason="invalid_credentials",
            status_code=403,
        )

    def test_login_page_renders_the_hidden_captcha_challenge(self) -> None:
        app = self._build_app()

        with TestClient(app) as client:
            response = client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="cb-honeypot"', response.text)
        self.assertIn('name="website"', response.text)
        self.assertTrue(self._extract_captcha_token(response.text))

    def test_filled_honeypot_is_rejected_before_password_validation(self) -> None:
        app = self._build_app()
        self.captcha_verifier.return_value = CaptchaOutcome.HONEYPOT_FILLED

        with (
            patch("app.routers.ui.auth.auth_service.authenticate", new=AsyncMock()) as authenticate,
            patch("app.routers.ui.auth.log_authentication_failure") as log_failure,
            TestClient(app) as client,
        ):
            login_page = client.get("/login")
            response = client.post(
                "/login",
                data={
                    "username": "admin",
                    "password": "Password123!",
                    "website": "https://spam.example",
                    "captcha_token": self._extract_captcha_token(login_page.text),
                    "csrf_token": extract_csrf_token(login_page.text),
                },
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("verify your submission.", response.text)
        authenticate.assert_not_awaited()
        log_failure.assert_called_once_with(
            unittest.mock.ANY,
            username=None,
            reason="anti_bot_rejected",
            status_code=403,
        )

    def test_successful_login_keeps_redirect_flow(self) -> None:
        app = self._build_app()
        user = SimpleNamespace(
            id=1,
            username="admin",
            password_hash="hashed-password",
            otp_secret=None,
            otp_enabled=False,
        )

        with (
            patch("app.routers.ui.auth.auth_service.authenticate", new=AsyncMock(return_value=user)),
            patch("app.routers.ui.auth.commit_and_flash", new=AsyncMock()) as commit_and_flash,
            patch("app.routers.ui.auth.initialize_user_session") as initialize_user_session,
            TestClient(app) as client,
        ):
            login_page = client.get("/login")
            csrf_token = extract_csrf_token(login_page.text)
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
        initialize_user_session.assert_called_once_with(unittest.mock.ANY, user)
        commit_and_flash.assert_awaited_once()

    def test_password_login_with_otp_redirects_to_second_factor_without_session(self) -> None:
        app = self._build_app()
        user = SimpleNamespace(
            id=1,
            username="admin",
            password_hash="hashed-password",
            otp_secret="encrypted-secret",
            otp_enabled=True,
        )

        with (
            patch("app.routers.ui.auth.auth_service.authenticate", new=AsyncMock(return_value=user)),
            patch("app.routers.ui.auth.initialize_user_session") as initialize_user_session,
            patch("app.routers.ui.auth.user_repository.update_last_login", new=AsyncMock()) as update_last_login,
            TestClient(app) as client,
        ):
            login_page = client.get("/login")
            response = client.post(
                "/login",
                data={
                    "username": "admin",
                    "password": "Password123!",
                    "next": "/sites",
                    "csrf_token": extract_csrf_token(login_page.text),
                },
                follow_redirects=False,
            )
            otp_page = client.get("/login/otp")

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login/otp")
        self.assertEqual(otp_page.status_code, 200)
        self.assertIn("Authentication code", otp_page.text)
        initialize_user_session.assert_not_called()
        update_last_login.assert_not_awaited()

    def test_successful_otp_login_issues_session_only_after_second_factor(self) -> None:
        app = self._build_app()
        user = SimpleNamespace(
            id=1,
            username="admin",
            password_hash="hashed-password",
            otp_secret="encrypted-secret",
            otp_enabled=True,
        )

        with (
            patch("app.routers.ui.auth.auth_service.authenticate", new=AsyncMock(return_value=user)),
            patch("app.routers.ui.auth.auth_service.verify_otp_factor", new=AsyncMock(return_value="totp")),
            patch("app.routers.ui.auth.user_repository.get_by_id", new=AsyncMock(return_value=user)),
            patch("app.routers.ui.auth.user_repository.update_last_login", new=AsyncMock()) as update_last_login,
            patch("app.routers.ui.auth.initialize_user_session") as initialize_user_session,
            patch("app.routers.ui.auth.commit_and_flash", new=AsyncMock()) as commit_and_flash,
            TestClient(app) as client,
        ):
            login_page = client.get("/login")
            client.post(
                "/login",
                data={
                    "username": "admin",
                    "password": "Password123!",
                    "next": "/sites",
                    "csrf_token": extract_csrf_token(login_page.text),
                },
                follow_redirects=False,
            )
            otp_page = client.get("/login/otp")
            response = client.post(
                "/login/otp",
                data={"code": "123456", "csrf_token": extract_csrf_token(otp_page.text)},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        update_last_login.assert_awaited_once()
        initialize_user_session.assert_called_once_with(unittest.mock.ANY, user)
        commit_and_flash.assert_awaited_once()

    def test_otp_challenge_is_rejected_after_authentication_state_changes(self) -> None:
        app = self._build_app()
        challenged_user = SimpleNamespace(
            id=1,
            username="admin",
            password_hash="original-hash",
            otp_secret="encrypted-secret",
            otp_enabled=True,
        )
        changed_user = SimpleNamespace(
            id=1,
            username="admin",
            password_hash="changed-hash",
            otp_secret="encrypted-secret",
            otp_enabled=True,
        )

        with (
            patch("app.routers.ui.auth.auth_service.authenticate", new=AsyncMock(return_value=challenged_user)),
            patch("app.routers.ui.auth.user_repository.get_by_id", new=AsyncMock(return_value=changed_user)),
            patch("app.routers.ui.auth.auth_service.verify_otp_factor", new=AsyncMock()) as verify_otp_factor,
            TestClient(app) as client,
        ):
            login_page = client.get("/login")
            client.post(
                "/login",
                data={
                    "username": "admin",
                    "password": "Password123!",
                    "next": "/sites",
                    "csrf_token": extract_csrf_token(login_page.text),
                },
                follow_redirects=False,
            )
            otp_page = client.get("/login/otp")
            response = client.post(
                "/login/otp",
                data={"code": "123456", "csrf_token": extract_csrf_token(otp_page.text)},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")
        verify_otp_factor.assert_not_awaited()

    def test_pending_otp_challenge_can_be_cancelled(self) -> None:
        app = self._build_app()
        challenged_user = SimpleNamespace(
            id=1,
            username="admin",
            password_hash="password-hash",
            otp_secret="encrypted-secret",
            otp_enabled=True,
        )

        with (
            patch("app.routers.ui.auth.auth_service.authenticate", new=AsyncMock(return_value=challenged_user)),
            patch("app.routers.ui.auth.user_repository.exists_any", new=AsyncMock(return_value=True)),
            TestClient(app) as client,
        ):
            login_page = client.get("/login")
            client.post(
                "/login",
                data={
                    "username": "admin",
                    "password": "Password123!",
                    "next": "/",
                    "csrf_token": extract_csrf_token(login_page.text),
                },
                follow_redirects=False,
            )
            otp_page = client.get("/login/otp")
            self.assertIn("Cancel and return to sign in", otp_page.text)
            cancel = client.post(
                "/login/otp/cancel",
                data={"csrf_token": extract_csrf_token(otp_page.text)},
                follow_redirects=False,
            )
            login_again = client.get("/login", follow_redirects=False)
            otp_after_cancel = client.get("/login/otp", follow_redirects=False)

        self.assertEqual(cancel.status_code, 303)
        self.assertEqual(cancel.headers["location"], "/login")
        self.assertEqual(login_again.status_code, 200)
        self.assertEqual(otp_after_cancel.status_code, 303)
        self.assertEqual(otp_after_cancel.headers["location"], "/login")

    def test_logout_redirects_without_sign_out_toast(self) -> None:
        app = self._build_app()
        user = SimpleNamespace(id=1, username="admin")

        with TestClient(app) as client:
            login_page = client.get("/login")
            csrf_token = extract_csrf_token(login_page.text)
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

        with (
            patch("app.routers.ui.auth.user_repository.exists_any", new=AsyncMock(return_value=False)),
            TestClient(app) as client,
        ):
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
            TestClient(app) as client,
        ):
            login_page = client.get("/login")
            csrf_token = extract_csrf_token(login_page.text)
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
        init_session.assert_called_once_with(unittest.mock.ANY, created_user)

    def test_setup_redirects_to_login_when_admin_creation_returns_none(self) -> None:
        app = self._build_app()

        with (
            patch("app.routers.ui.auth.user_repository.exists_any", new=AsyncMock(return_value=False)),
            patch("app.routers.ui.auth.auth_service.ensure_default_admin", new=AsyncMock(return_value=None)),
            TestClient(app) as client,
        ):
            login_page = client.get("/login")
            csrf_token = extract_csrf_token(login_page.text)
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

        with (
            patch("app.routers.ui.auth.user_repository.exists_any", new=AsyncMock(side_effect=[True, True])),
            TestClient(app) as client,
        ):
            login_page = client.get("/login")
            csrf_token = extract_csrf_token(login_page.text)
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

        with (
            patch("app.routers.ui.auth.user_repository.exists_any", new=AsyncMock(return_value=False)),
            TestClient(app) as client,
        ):
            login_page = client.get("/login")
            csrf_token = extract_csrf_token(login_page.text)
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

        with (
            patch("app.routers.ui.auth.user_repository.exists_any", new=AsyncMock(return_value=False)),
            TestClient(app) as client,
        ):
            login_page = client.get("/login")
            csrf_token = extract_csrf_token(login_page.text)
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
