#!/usr/bin/env python3
#
# tests/test_ui_settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from app.config.limiter import limiter
from app.config.settings import get_settings
from tests.env_overrides import ModuleEnv

_ENV = ModuleEnv()

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database.session import get_db_session
from app.routers.ui.settings import router as settings_router
from app.services.passkeys import MAX_PASSKEYS_PER_USER
from app.services.runtime_settings import SSLLABS_RETENTION_DEFAULT_DAYS
from tests.ui_test_app import build_ui_test_app, extract_csrf_token


def tearDownModule() -> None:
    _ENV.restore()


class UISettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        _ENV.apply()
        self.onboarding_patcher = patch(
            "app.routers.ui._common.get_onboarding_state",
            new=AsyncMock(return_value=SimpleNamespace(status="completed")),
        )
        self.onboarding_patcher.start()
        # The Passkey tab lists the current user's passkeys; the mocked session
        # cannot answer that, so tests opt in explicitly where it matters.
        self.passkey_list_patcher = patch(
            "app.routers.ui.settings.passkey_service.list_for_user",
            new=AsyncMock(return_value=[]),
        )
        self.passkey_list = self.passkey_list_patcher.start()
        self.retention_patcher = patch(
            "app.routers.ui.settings.get_ssllabs_history_retention_days",
            new=AsyncMock(return_value=SSLLABS_RETENTION_DEFAULT_DAYS),
        )
        self.retention_patcher.start()
        self.maintenance_page_patcher = patch(
            "app.routers.ui.settings.get_maintenance_page_html",
            new=AsyncMock(return_value="<h1>This Service is currently not available</h1>"),
        )
        self.maintenance_page_patcher.start()
        # Saving an SSL Labs email starts the real scheduler, which would open the real database.
        self.ssllabs_startup_patcher = patch(
            "app.routers.ui.settings.ssllabs_service.startup",
            new=AsyncMock(),
        )
        self.ssllabs_startup_patcher.start()

    def tearDown(self) -> None:
        self.onboarding_patcher.stop()
        self.passkey_list_patcher.stop()
        self.retention_patcher.stop()
        self.maintenance_page_patcher.stop()
        self.ssllabs_startup_patcher.stop()
        get_settings.cache_clear()

    @staticmethod
    async def _session_override():
        yield AsyncMock()

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

    def _build_action_app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(settings_router)
        app.dependency_overrides[get_db_session] = self._session_override
        return app

    def test_settings_page_uses_tabs_for_navigation(self) -> None:
        template = Path("app/templates/settings.html").read_text(encoding="utf-8")
        css = Path("app/static/css/app.css").read_text(encoding="utf-8")

        self.assertIn('class="nav settings-tabs mb-4"', template)
        self.assertIn('id="settingsGeneralTab"', template)
        self.assertIn('id="settingsSecurityTab"', template)
        self.assertIn('id="settingsPasskeyTab"', template)
        self.assertIn('id="settingsSslLabsTab"', template)
        self.assertIn('class="tab-content" id="settingsTabContent"', template)
        self.assertIn(".settings-tabs .nav-link.active {", css)

    def test_settings_page_renders_passkey_add_modal(self) -> None:
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
            TestClient(app) as client,
        ):
            response = client.get("/settings")

        self.assertEqual(response.status_code, 200)
        # The modal markup, its trigger, and the register button must all be
        # present exactly once and in document order (trigger before modal).
        self.assertEqual(response.text.count('id="addPasskeyModal"'), 1)
        self.assertEqual(response.text.count("data-passkey-register-button"), 1)
        self.assertIn('data-bs-target="#addPasskeyModal"', response.text)
        self.assertLess(
            response.text.index('data-bs-target="#addPasskeyModal"'),
            response.text.index('id="addPasskeyModal"'),
        )
        # Below the limit, the trigger opens the modal instead of being disabled.
        trigger_start = response.text.index('data-bs-target="#addPasskeyModal"')
        trigger_tag = response.text[max(0, trigger_start - 200):trigger_start]
        self.assertNotIn("disabled", trigger_tag[trigger_tag.rindex("<button") :])

    def test_settings_page_disables_passkey_add_trigger_when_limit_reached(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        now = datetime.now(UTC)
        self.passkey_list.return_value = [
            SimpleNamespace(
                id=index,
                device_name=f"Device {index}",
                transports=None,
                created_at=now,
                last_used_at=None,
            )
            for index in range(MAX_PASSKEYS_PER_USER)
        ]

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
            TestClient(app) as client,
        ):
            response = client.get("/settings")

        self.assertEqual(response.status_code, 200)
        # At the limit, the trigger must be disabled and not wired to open the
        # modal, and the reason must be visible text, not just a title attribute
        # (title tooltips are unreachable on disabled buttons and on touch).
        self.assertNotIn('data-bs-target="#addPasskeyModal"', response.text)
        self.assertIn(
            f"The maximum of {MAX_PASSKEYS_PER_USER} passkeys is registered. Remove one to add another.",
            response.text,
        )
        self.assertIn('id="passkey-limit-hint"', response.text)
        self.assertIn('aria-describedby="passkey-limit-hint"', response.text)

    def test_passkey_deletion_rejects_an_incorrect_current_password(self) -> None:
        app = self._build_action_app()
        current_user = SimpleNamespace(id=7, username="admin", role="admin", password_hash="stored-hash")

        with (
            patch("app.routers.ui.settings.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.settings.validated_csrf_form",
                new=AsyncMock(return_value={"current_password": "wrong-password"}),
            ),
            patch("app.routers.ui.settings.auth_service.verify_password", new=AsyncMock(return_value=False)) as verify,
            patch("app.routers.ui.settings.passkey_service.delete_for_user", new=AsyncMock()) as delete,
            TestClient(app) as client,
        ):
            response = client.post(
                "/settings/passkeys/42/delete",
                headers={"Accept": "application/json"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"success": False, "message": "Current password is incorrect."})
        verify.assert_awaited_once_with("wrong-password", "stored-hash")
        delete.assert_not_awaited()

    def test_passkey_deletion_accepts_the_current_password(self) -> None:
        app = self._build_action_app()
        current_user = SimpleNamespace(id=7, username="admin", role="admin", password_hash="stored-hash")

        with (
            patch("app.routers.ui.settings.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.settings.validated_csrf_form",
                new=AsyncMock(return_value={"current_password": "correct-password"}),
            ),
            patch("app.routers.ui.settings.auth_service.verify_password", new=AsyncMock(return_value=True)) as verify,
            patch(
                "app.routers.ui.settings.passkey_service.delete_for_user",
                new=AsyncMock(return_value=True),
            ) as delete,
            TestClient(app) as client,
        ):
            response = client.post(
                "/settings/passkeys/42/delete",
                headers={"Accept": "application/json"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True, "message": "Passkey removed."})
        verify.assert_awaited_once_with("correct-password", "stored-hash")
        delete.assert_awaited_once_with(ANY, current_user, 42)

    def test_two_factor_setup_page_is_not_cacheable(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(
            id=1,
            username="admin",
            role="admin",
            password_hash="password-hash",
            otp_secret="encrypted-secret",
            otp_enabled=False,
        )

        with (
            patch("app.routers.ui.settings.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.settings.auth_service.pending_otp_secret", return_value="OTPSECRET"),
            patch("app.routers.ui.settings.auth_service.provisioning_uri", return_value="otpauth://totp/test"),
            patch("app.routers.ui.settings.provisioning_qr_data_url", return_value="data:image/png;base64,AA=="),
            TestClient(app) as client,
        ):
            response = client.get("/settings/two-factor")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("OTPSECRET", response.text)

    def test_desktop_settings_header_uses_the_dashboard_page_gap(self) -> None:
        template = Path("app/templates/settings.html").read_text(encoding="utf-8")
        css = Path("app/static/css/app.css").read_text(encoding="utf-8")

        self.assertIn('class="app-page app-page--settings"', template)
        self.assertIn(
            "--cb-page-header-content-gap: 2.2rem;",
            css,
            "Settings must inherit the dashboard-derived page-header spacing token.",
        )
        self.assertIn(".app-grid> :first-child {\n    padding-inline-start: 0;\n}", css)
        self.assertNotIn(".app-page--settings .app-page__header {\n        margin-bottom:", css)

    def test_mobile_settings_tab_grid_does_not_overflow_the_clipped_page(self) -> None:
        css = Path("app/static/css/app.css").read_text(encoding="utf-8")

        self.assertIn(
            ".app-page--settings .tab-pane>.row {\n        margin-inline: 0;\n    }",
            css,
            "Below lg the settings rows must not extend past the clipped .app-page box.",
        )
        self.assertIn(
            '.app-page--settings .tab-pane>.row>[class*="col-"] {\n        padding-inline: 0;\n    }',
            css,
            "Full-width settings columns must drop the gutter padding so panels stay aligned with the header.",
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
            TestClient(app) as client,
        ):
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
        self.assertLess(response.text.index("Global Settings"), response.text.index("Change Password"))
        self.assertLess(response.text.index("Change Password"), response.text.index("SSL Labs API"))
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
        self.assertIn('class="panel-card settings-panel settings-panel--primary"', response.text)
        self.assertIn('id="ssllabs-retention-settings"', response.text)
        self.assertIn("How long SSL Labs grade history samples are kept for the dashboard chart.", response.text)
        self.assertNotIn("How long daily SSL Labs grade history is kept for the dashboard chart.", response.text)
        self.assertIn('class="ssllabs-retention-scale"', response.text, "Retention slider must have scale wrapper for tick labels")
        self.assertIn('class="ssllabs-retention-track"', response.text, "Retention slider must have a positioned track wrapper for the tick bubbles")
        self.assertIn('class="ssllabs-retention-ticks"', response.text, "Retention slider must have a tick bubble overlay container")
        self.assertIn('class="ssllabs-retention-tick"', response.text, "Retention slider must have individual tick bubbles")
        self.assertIn('class="ssllabs-retention-labels"', response.text, "Retention slider must have tick label container")
        self.assertIn('class="ssllabs-retention-label"', response.text, "Retention slider must have individual tick labels")
        # Factory default retention is unlimited (0 days), the first slider position.
        self.assertIn('value="0"', response.text)
        self.assertEqual(response.text.count('class="ssllabs-retention-tick"'), 7)
        self.assertEqual(response.text.count('class="ssllabs-retention-label"'), 7)
        self.assertIn('class="badge cb-pill text-bg-secondary" id="ssllabs-retention-badge"', response.text)
        # Tick labels must use human-readable names matching SSLLABS_RETENTION_DAY_VALUES
        self.assertIn("&infin;", response.text, "Tick label for 0 days must render as the infinity symbol")
        self.assertIn("7d", response.text, "Tick label for 7 days must render as '7d'")
        self.assertIn("14d", response.text, "Tick label for 14 days must render as '14d'")
        self.assertIn("30d", response.text, "Tick label for 30 days must render as '30d'")
        self.assertIn("90d", response.text, "Tick label for 90 days must render as '90d'")
        self.assertIn("180d", response.text, "Tick label for 180 days must render as '180d'")
        self.assertIn("1y", response.text, "Tick label for 365 days must render as '1y'")
        self.assertNotIn("365d", response.text, "365 days must not appear as '365d' — use '1y'")
        # Slider range must span exactly 0 … len(values)-1 to match the label grid
        self.assertRegex(response.text, r'min="0"\s+max="6"', "Slider range must cover 7 steps (0-6) for the 7 retention values")
        # data-retention-values must expose the full allowed set for the JS formatLabel
        self.assertIn('data-retention-values="[0, 7, 14, 30, 90, 180, 365]"', response.text, "data-retention-values must list all allowed retention day counts")

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
            TestClient(app) as client,
        ):
            page = client.get("/settings")
            csrf_token = extract_csrf_token(page.text)
            response = client.post(
                "/settings/onboarding/restart",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/onboarding")
        reset_onboarding_state.assert_awaited_once()

    def test_settings_page_renders_maintenance_page_editor(self) -> None:
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
            TestClient(app) as client,
        ):
            response = client.get("/settings")

        self.assertEqual(response.status_code, 200)
        general_panel = response.text[
            response.text.index('id="settingsGeneralPanel"'):response.text.index('id="settingsSecurityPanel"')
        ]
        self.assertIn('action="/settings/maintenance-page"', general_panel)
        self.assertIn('contenteditable="true"', general_panel)
        self.assertIn('aria-labelledby="maintenance-page-label"', general_panel)
        # The stored HTML is passed escaped to the no-JS textarea fallback, never rendered raw.
        self.assertIn(
            "data-maintenance-editor-source>&lt;h1&gt;This Service is currently not available&lt;/h1&gt;</textarea>",
            general_panel,
        )
        self.assertIn("/static/js/maintenance-editor.js", response.text)

    def _post_maintenance_page(self, *, sites, set_side_effect=None, deploy_result=(True, "ok")):
        app = self._build_action_app()
        session = AsyncMock()

        async def session_override():
            yield session

        app.dependency_overrides[get_db_session] = session_override
        current_user = SimpleNamespace(username="admin", role="admin")
        with (
            patch("app.routers.ui.settings.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.settings.validated_csrf_form",
                new=AsyncMock(return_value={"maintenance_page_html": "<h1>Back soon</h1>"}),
            ),
            patch(
                "app.routers.ui.settings.set_maintenance_page_html",
                new=AsyncMock(side_effect=set_side_effect),
            ) as set_page,
            patch("app.routers.ui.settings.site_repository.list_all", new=AsyncMock(return_value=sites)),
            patch(
                "app.routers.ui.settings.validate_and_deploy_full_caddyfile",
                new=AsyncMock(return_value=deploy_result),
            ) as deploy,
            TestClient(app) as client,
        ):
            response = client.post(
                "/settings/maintenance-page",
                headers={"Accept": "application/json"},
            )
        return response, session, set_page, deploy

    def test_maintenance_page_save_skips_deploy_without_stopped_sites(self) -> None:
        response, session, set_page, deploy = self._post_maintenance_page(
            sites=[SimpleNamespace(maintenance_mode=False)],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True, "message": "Maintenance page saved."})
        set_page.assert_awaited_once_with(ANY, "<h1>Back soon</h1>")
        deploy.assert_not_awaited()
        session.commit.assert_awaited_once()

    def test_maintenance_page_save_redeploys_stopped_sites_and_rolls_back_on_failure(self) -> None:
        response, session, _set_page, deploy = self._post_maintenance_page(
            sites=[SimpleNamespace(maintenance_mode=True)],
            deploy_result=(False, "Rendered Caddy configuration is invalid."),
        )

        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.json()["success"])
        self.assertIn("Rendered Caddy configuration is invalid.", response.json()["message"])
        deploy.assert_awaited_once()
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()

    def test_maintenance_page_save_rejects_invalid_content(self) -> None:
        response, session, _set_page, deploy = self._post_maintenance_page(
            sites=[],
            set_side_effect=ValueError("The maintenance page must not be empty."),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["message"], "The maintenance page must not be empty.")
        deploy.assert_not_awaited()
        session.commit.assert_not_awaited()

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
            TestClient(app) as client,
        ):
            page = client.get("/settings")
            csrf_token = extract_csrf_token(page.text)
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
            TestClient(app) as client,
        ):
            page = client.get("/settings")
            csrf_token = extract_csrf_token(page.text)
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
                    csrf_token = extract_csrf_token(page.text)
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

    def test_settings_page_enabling_rate_limit_does_not_crash_request_in_flight(self) -> None:
        # Regression test: slowapi's `@limiter.limit` wrapper reads the shared
        # `limiter.enabled` flag both before and after the handler body runs.
        # If the handler itself flips `limiter.enabled` from False to True
        # synchronously (as this endpoint's rate-limit checkbox does), the
        # wrapper's post-call check sees `enabled=True` and tries to read
        # `request.state.view_rate_limit`, which was never set because the
        # pre-call check saw `enabled=False` and skipped binding `request`.
        # This must not raise UnboundLocalError; the toggle is deferred to a
        # background task that runs after slowapi's wrapper has returned.
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
            patch("app.routers.ui.settings.set_caddy_config", new=AsyncMock()),
            patch("app.routers.ui.settings.set_rate_limit_enabled", new=AsyncMock()),
        ):
            limiter.enabled = False
            try:
                with TestClient(app, raise_server_exceptions=True) as client:
                    page = client.get("/settings")
                    csrf_token = extract_csrf_token(page.text)
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
                self.assertTrue(limiter.enabled)
            finally:
                limiter.enabled = original_rate_limit_enabled

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
            TestClient(app) as client,
        ):
            page = client.get("/settings")
            csrf_token = extract_csrf_token(page.text)
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
            TestClient(app) as client,
        ):
            page = client.get("/settings")
            csrf_token = extract_csrf_token(page.text)
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
            TestClient(app) as client,
        ):
            page = client.get("/settings")
            csrf_token = extract_csrf_token(page.text)
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
            TestClient(app) as client,
        ):
            page = client.get("/settings")
            csrf_token = extract_csrf_token(page.text)
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
            TestClient(app) as client,
        ):
            page = client.get("/settings")
            csrf_token = extract_csrf_token(page.text)
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
            TestClient(app) as client,
        ):
            page = client.get("/settings")
            csrf_token = extract_csrf_token(page.text)
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
            TestClient(app) as client,
        ):
            page = client.get("/settings")
            csrf_token = extract_csrf_token(page.text)
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
        initialize_session.assert_called_once_with(unittest.mock.ANY, current_user)
