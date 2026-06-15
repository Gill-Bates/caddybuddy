#!/usr/bin/env python3
#
# tests/test_ui_caddyfile.py
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
    def _session_override_for(session):
        async def _override():
            yield session

        return _override

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

    def _build_app_with_session(self, session):
        return build_ui_test_app(
            caddyfile_router,
            session_override=self._session_override_for(session),
            stub_routes=[
                ("GET", "/", "home_page"),
                ("GET", "/sites", "sites_page"),
                ("POST", "/logout", "logout_action"),
            ],
        )

    def _assert_response_has_csp_nonce(self, response) -> None:
        header = response.headers["content-security-policy"]
        match = re.search(r"style-src 'self' 'nonce-([^']+)'", header)
        if not match:
            raise AssertionError(f"CSP nonce missing from header: {header}")
        nonce = match.group(1)
        self.assertIn(f'<meta name="csp-nonce" content="{nonce}">', response.text)

    @staticmethod
    def _extract_csrf_token(html: str) -> str:
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if match is None:
            raise AssertionError("csrf_token input not found in caddyfile page")
        return match.group(1)

    def test_caddyfile_desktop_css_keeps_card_shadow_inside_safe_gutter(self) -> None:
        css_path = Path(__file__).resolve().parents[1] / "app/static/css/app.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn(
            ".app-page--caddyfile > .app-grid > .col-12 {\n        display: flex;\n        flex: 1 1 0;\n        flex-direction: column;\n        min-height: 0;\n        overflow: visible;\n        padding: 0.35rem;\n        margin: -0.35rem;\n    }",
            css,
            "Desktop Caddyfile column must keep a small gutter so the panel shadow is not clipped.",
        )
        self.assertIn(
            "body:has(.app-page--caddyfile) .app-container {\n        display: flex;\n        flex-direction: column;\n        flex: 1 1 0;\n        min-height: 0;\n        height: 100%;\n        overflow: visible;\n    }",
            css,
            "Desktop Caddyfile container must not clip the panel shadow.",
        )
        self.assertIn(
            ".app-page--caddyfile .caddyfile-editor-panel {\n        display: flex;\n        flex-direction: column;\n        width: 100%;\n        flex: 1 1 auto;\n        min-height: 30rem;\n        height: auto;\n        overflow: hidden;\n        align-self: stretch;\n    }",
            css,
            "Desktop Caddyfile panel must match the stable full-height card layout used on other pages.",
        )

    def test_caddyfile_mobile_css_keeps_horizontal_editor_scroll(self) -> None:
        css_path = Path(__file__).resolve().parents[1] / "app/static/css/app.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn(
            "@media (max-width: 767.98px) {\n    .caddyfile-editor-panel__textarea,\n    #site-caddy-directives {\n        white-space: pre;\n        overflow-wrap: normal;\n        overflow-x: auto;\n        min-height: 18rem;\n        -webkit-overflow-scrolling: touch;\n    }\n\n    .caddyfile-editor-panel .cm-scroller,\n    .sites-form-panel__config .cm-scroller {\n        width: 100%;\n        min-width: 0;\n        overflow-x: auto;\n        overflow-y: auto;\n        -webkit-overflow-scrolling: touch;\n    }\n\n    .caddyfile-editor-panel .cm-content,\n    .sites-form-panel__config .cm-content {\n        min-width: 0;\n    }\n\n    .caddyfile-editor-panel__actions {\n        min-width: 0;\n    }\n}",
            css,
            "Mobile code editors must keep horizontal swipe/scroll instead of forcing wrapped lines.",
        )
        self.assertIn(
            "@media (max-width: 767.98px) {\n    .app-main {\n        padding-top: calc(var(--cb-mobile-topbar-height) + 0.75rem);\n    }",
            css,
            "Mobile pages must keep balanced spacing below the fixed top bar.",
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
        self.assertIn('class="caddyfile-editor-panel__form"', response.text)
        self.assertIn('class="form-control font-monospace caddyfile-editor-panel__textarea"', response.text)
        self.assertIn("/static/vendor/codemirror/caddybuddy-codemirror.js", response.text)
        self.assertIn("data-caddyfile-config-form", response.text)
        self.assertIn('id="caddyfile-validate-btn"', response.text)
        self.assertIn('data-validate-error-prefix="Caddyfile invalid"', response.text)
        self.assertRegex(
            response.text,
            r'id="caddyfile-validate-btn"[^>]*disabled[^>]*aria-disabled="true"',
        )
        self._assert_response_has_csp_nonce(response)
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
        self._assert_response_has_csp_nonce(response)
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

    def test_caddyfile_page_falls_back_to_configured_default_baseline(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        runtime_status = SimpleNamespace(
            onboarding_required=False,
            caddyfile_path="/app/Caddyfile",
            admin_api_reachable=True,
        )
        configured_default = "{\n    email ops@example.com\n}"

        with (
            patch("app.routers.ui.caddyfile.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.caddyfile.get_baseline_caddyfile", new=AsyncMock(return_value="")),
            patch("app.routers.ui.caddyfile.get_caddy_runtime_status", new=AsyncMock(return_value=runtime_status)),
            patch(
                "app.routers.ui.caddyfile.get_settings",
                return_value=SimpleNamespace(caddy_baseline_caddyfile=configured_default),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/caddyfile")

        self.assertEqual(response.status_code, 200)
        self.assertIn("email ops@example.com", response.text)

    def test_caddyfile_page_redirects_non_admin_user(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="operator", role="user")

        with patch("app.routers.ui.caddyfile.require_user", new=AsyncMock(return_value=current_user)):
            with TestClient(app) as client:
                response = client.get("/caddyfile", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")

    def test_validate_caddyfile_rejects_non_admin_user(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="operator", role="user")

        with (
            patch("app.routers.ui.caddyfile.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.caddyfile.validated_form", new=AsyncMock(return_value={"caddyfile": "example.com { respond \"ok\" }"})),
            patch("app.middleware.csrf.validate_csrf_token"),
        ):
            with TestClient(app) as client:
                response = client.post("/caddyfile/validate")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"valid": False, "message": "Administrator access is required."})

    def test_run_onboarding_commits_and_publishes_event_on_success(self) -> None:
        session = AsyncMock()
        app = self._build_app_with_session(session)
        current_user = SimpleNamespace(username="admin", role="admin")
        result = SimpleNamespace(status="onboarded", error=None)
        runtime_status = SimpleNamespace(
            onboarding_required=True,
            caddyfile_path="/app/Caddyfile",
            admin_api_reachable=True,
        )

        with (
            patch("app.routers.ui.caddyfile.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.caddyfile.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.caddyfile.get_baseline_caddyfile", new=AsyncMock(return_value="")),
            patch("app.routers.ui.caddyfile.get_caddy_runtime_status", new=AsyncMock(return_value=runtime_status)),
            patch("app.routers.ui.caddyfile.onboard_caddy", new=AsyncMock(return_value=result)),
            patch("app.routers.ui.caddyfile.publish_resource_event", new=AsyncMock()) as publish_event,
        ):
            with TestClient(app) as client:
                page = client.get("/caddyfile")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/caddyfile/onboard",
                    data={"csrf_token": csrf_token},
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/caddyfile")
        session.commit.assert_awaited_once()
        session.rollback.assert_not_called()
        publish_event.assert_awaited_once_with("caddyfile", "onboarded", "primary")

    def test_run_onboarding_commits_without_event_when_already_managed(self) -> None:
        session = AsyncMock()
        app = self._build_app_with_session(session)
        current_user = SimpleNamespace(username="admin", role="admin")
        result = SimpleNamespace(status="already_managed", error=None)
        runtime_status = SimpleNamespace(
            onboarding_required=True,
            caddyfile_path="/app/Caddyfile",
            admin_api_reachable=True,
        )

        with (
            patch("app.routers.ui.caddyfile.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.caddyfile.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.caddyfile.get_baseline_caddyfile", new=AsyncMock(return_value="")),
            patch("app.routers.ui.caddyfile.get_caddy_runtime_status", new=AsyncMock(return_value=runtime_status)),
            patch("app.routers.ui.caddyfile.onboard_caddy", new=AsyncMock(return_value=result)),
            patch("app.routers.ui.caddyfile.publish_resource_event", new=AsyncMock()) as publish_event,
        ):
            with TestClient(app) as client:
                page = client.get("/caddyfile")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/caddyfile/onboard",
                    data={"csrf_token": csrf_token},
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/caddyfile")
        session.commit.assert_awaited_once()
        session.rollback.assert_not_called()
        publish_event.assert_not_called()

    def test_run_onboarding_rolls_back_without_event_on_failure(self) -> None:
        session = AsyncMock()
        app = self._build_app_with_session(session)
        current_user = SimpleNamespace(username="admin", role="admin")
        result = SimpleNamespace(status="error", error="boom")
        runtime_status = SimpleNamespace(
            onboarding_required=True,
            caddyfile_path="/app/Caddyfile",
            admin_api_reachable=True,
        )

        with (
            patch("app.routers.ui.caddyfile.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.caddyfile.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.caddyfile.get_baseline_caddyfile", new=AsyncMock(return_value="")),
            patch("app.routers.ui.caddyfile.get_caddy_runtime_status", new=AsyncMock(return_value=runtime_status)),
            patch("app.routers.ui.caddyfile.onboard_caddy", new=AsyncMock(return_value=result)),
            patch("app.routers.ui.caddyfile.publish_resource_event", new=AsyncMock()) as publish_event,
        ):
            with TestClient(app) as client:
                page = client.get("/caddyfile")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/caddyfile/onboard",
                    data={"csrf_token": csrf_token},
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/caddyfile")
        session.rollback.assert_awaited_once()
        session.commit.assert_not_called()
        publish_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
