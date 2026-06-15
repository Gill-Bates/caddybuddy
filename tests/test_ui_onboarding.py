#!/usr/bin/env python3
#
# tests/test_ui_onboarding.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
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
from app.routers.ui.onboarding import router as onboarding_router
from app.services.caddy_onboarding import OnboardingWizardState
from tests.ui_test_app import build_ui_test_app


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


class UIOnboardingTests(unittest.TestCase):
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
            onboarding_router,
            session_override=self._session_override,
            stub_routes=[
                ("GET", "/", "home_page"),
                ("GET", "/caddyfile", "caddyfile_page"),
                ("GET", "/sites", "sites_page"),
                ("POST", "/logout", "logout_action"),
            ],
        )

    def test_completed_onboarding_redirects_to_home(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.onboarding.get_onboarding_state",
                new=AsyncMock(return_value=SimpleNamespace(status="completed")),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/onboarding", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")

    def test_completed_onboarding_redirects_even_when_runtime_still_needs_onboarding(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.onboarding.get_onboarding_state",
                new=AsyncMock(return_value=SimpleNamespace(status="completed")),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/onboarding", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")

    def test_onboarding_page_renders_without_sidebar(self) -> None:
        """Linting rule: onboarding wizard must render in full-width shell without navigation sidebar."""
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        state = OnboardingWizardState(status="in_progress")

        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.get_onboarding_state", new=AsyncMock(return_value=state)),
            patch(
                "app.routers.ui.onboarding.get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019", caddyfile_path_str="/app/Caddyfile")),
            ),
            patch("app.routers.ui.onboarding.get_ssllabs_email", new=AsyncMock(return_value="")),
        ):
            with TestClient(app) as client:
                response = client.get("/onboarding")

        self.assertEqual(response.status_code, 200)
        self.assertIn("app-shell--public", response.text, "Onboarding must use full-width public shell (no sidebar)")
        self.assertNotIn('<aside class="app-sidebar"', response.text, "Sidebar <aside> must be hidden during onboarding")

    def test_onboarding_wizard_structural_elements(self) -> None:
        """Linting rule: wizard must contain viewport, track, step buttons, nav buttons, and step panels."""
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        # pending_location="host" so step 2 renders the source form (not the locked fallback)
        state = OnboardingWizardState(status="in_progress", pending_location="host")

        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.get_onboarding_state", new=AsyncMock(return_value=state)),
            patch(
                "app.routers.ui.onboarding.get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019", caddyfile_path_str="/app/Caddyfile")),
            ),
            patch("app.routers.ui.onboarding.get_ssllabs_email", new=AsyncMock(return_value="")),
        ):
            with TestClient(app) as client:
                response = client.get("/onboarding")

        html = response.text
        self.assertEqual(response.status_code, 200)

        # Wizard root
        self.assertIn("data-onboarding-wizard", html, "Root wizard element must have data-onboarding-wizard")
        self.assertIn("data-current-step=", html, "Wizard must carry data-current-step")
        self.assertIn("data-max-step=", html, "Wizard must carry data-max-step")

        # Viewport / track (required for swipe + CSS scroll)
        self.assertIn("data-wizard-viewport", html, "Wizard must have a scroll viewport element")
        self.assertIn("data-wizard-track", html, "Wizard must have a track element inside viewport")

        # Step buttons — 4-step wizard
        self.assertNotIn('role="tablist"', html, "Wizard steps should not announce incomplete tab semantics")
        self.assertIn("data-wizard-step-button", html, "Step buttons must carry data-wizard-step-button")
        self.assertIn('data-step-target="1"', html, "Step button for step 1 must be present")
        self.assertIn('data-step-target="2"', html, "Step button for step 2 must be present")
        self.assertIn('data-step-target="3"', html, "Step button for step 3 must be present")
        self.assertIn('data-step-target="4"', html, "Step button for step 4 must be present")
        # Runtime location is auto-detected — the manual question must be gone, the detected value shown.
        self.assertNotIn('name="runtime_location"', html, "Runtime location must be auto-detected, not asked")
        self.assertNotIn("Where does CaddyBuddy run?", html, "Manual runtime-location question must be removed")
        self.assertIn("data-onboarding-detected-runtime", html, "Detected CaddyBuddy environment must be displayed")
        self.assertIn("CaddyBuddy environment", html, "CaddyBuddy-environment label must be visible")
        self.assertIn("Where does Caddy run?", html, "Caddy-location question must be present in step 1")
        self.assertIn("What should CaddyBuddy start from?", html, "Caddy-source question must be present in step 2")
        self.assertIn('name="caddy_location"', html, "Step 1 must post a caddy_location answer")
        self.assertIn('name="caddy_source"', html, "Step 2 must post a caddy_source answer")

        # Redundant Previous/Next nav is removed; the stepper buttons drive navigation.
        self.assertNotIn("data-wizard-back", html, "Redundant Previous button must be removed")
        self.assertNotIn("data-wizard-next", html, "Redundant Next button must be removed")

        # Step panels
        self.assertIn('data-wizard-step data-step="1"', html, "Step 1 panel must be present")
        self.assertIn('data-wizard-step data-step="2"', html, "Step 2 panel must be present")
        self.assertIn('data-wizard-step data-step="3"', html, "Step 3 panel must be present")
        self.assertIn('data-wizard-step data-step="4"', html, "Step 4 panel must be present")

        # JS file loaded
        self.assertIn("onboarding-wizard.js", html, "Onboarding wizard JS must be included")

    def test_onboarding_wizard_aria_compliance(self) -> None:
        """Linting rule: wizard step buttons must use aria-current and aria-label for screen readers."""
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        state = OnboardingWizardState(status="in_progress")

        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.get_onboarding_state", new=AsyncMock(return_value=state)),
            patch(
                "app.routers.ui.onboarding.get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019", caddyfile_path_str="/app/Caddyfile")),
            ),
            patch("app.routers.ui.onboarding.get_ssllabs_email", new=AsyncMock(return_value="")),
        ):
            with TestClient(app) as client:
                response = client.get("/onboarding")

        html = response.text
        self.assertIn('aria-label="Onboarding steps"', html, "Step navigation must have aria-label")
        self.assertIn('aria-current="step"', html, "Active step button must have aria-current=step")
        self.assertEqual(
            html.count('cb-onboarding-wizard__step-button is-active'),
            1,
            "Factory-default onboarding must render exactly one active step button",
        )
        self.assertEqual(
            html.count('cb-onboarding-wizard__step-button is-inactive'),
            3,
            "Factory-default onboarding must render 3 locked steps as inactive (4-step wizard)",
        )
        self.assertRegex(
            html,
            r'class="cb-onboarding-wizard__step-button is-inactive"[^>]*data-step-target="2"[^>]*disabled aria-disabled="true"',
            "Step 2 must start inactive and disabled before step 1 is completed",
        )
        self.assertRegex(
            html,
            r'class="cb-onboarding-wizard__step-button is-inactive"[^>]*data-step-target="3"[^>]*disabled aria-disabled="true"',
            "Step 3 must start inactive and disabled before location+source are chosen",
        )
        self.assertRegex(
            html,
            r'class="cb-onboarding-wizard__step-button is-inactive"[^>]*data-step-target="4"[^>]*disabled aria-disabled="true"',
            "Step 4 must start inactive and disabled before preflight is completed",
        )
        self.assertIn('data-wizard-step data-step="2" hidden inert', html)
        self.assertIn('data-wizard-step data-step="3" hidden inert', html)
        self.assertIn('data-wizard-step data-step="4" hidden inert', html)

    def test_onboarding_wizard_js_preserves_locked_step_button_identity(self) -> None:
        """Linting rule: locked step buttons must not be clamped into the active step."""
        js_path = Path(__file__).resolve().parents[1] / "app/static/js/onboarding-wizard.js"
        js = js_path.read_text(encoding="utf-8")

        self.assertIn(
            'const stepTarget = Number.parseInt(button.dataset.stepTarget || "", 10);',
            js,
            "Step-button identity must be read before lock-state decisions",
        )
        self.assertIn(
            "const isActive = step === currentStep;",
            js,
            "Only the real step target may be compared with the current step",
        )
        self.assertNotIn(
            "clampStep(Number.parseInt(button.dataset.stepTarget",
            js,
            "Do not clamp data-step-target by maxStep; that makes all locked steps active at maxStep=1",
        )

    def test_onboarding_wizard_css_does_not_slide_hidden_panels_offscreen(self) -> None:
        """Linting rule: the enhanced track must not translate hidden panels off-screen.

        The JS hides non-active step panels with [hidden] (display:none). Laying the
        panels out side by side and sliding the track with translateX then pushes the
        single visible panel off-screen, blanking the viewport. Lock the show/hide
        layout so step 2 / preflight cannot render an empty gradient again.
        """
        css_path = Path(__file__).resolve().parents[1] / "app/static/css/app.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertNotIn(
            'cb-onboarding-wizard.is-enhanced[data-current-step=',
            css,
            "Per-step translateX slide selectors must be gone; they blank the viewport",
        )
        self.assertNotIn(
            ".cb-onboarding-wizard.is-enhanced .cb-onboarding-step {\n    flex: 0 0 100%;",
            css,
            "Step panels must not be laid out side by side for a horizontal slide",
        )
        self.assertIn(
            ".cb-onboarding-wizard.is-enhanced .cb-onboarding-wizard__track {\n    display: block;\n}",
            css,
            "Enhanced onboarding track must render the active panel in a single column",
        )

    def test_onboarding_step_three_renders_content_when_mode_selected(self) -> None:
        """Regression: selecting a mode must reveal step 3 (access check), not a blank panel."""
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        # mode set, no preflight yet -> server computes current_step == 3 (4-step wizard)
        state = OnboardingWizardState(status="in_progress", mode="host", runtime_location="host")

        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.get_onboarding_state", new=AsyncMock(return_value=state)),
            patch(
                "app.routers.ui.onboarding.get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019", caddyfile_path_str="/app/Caddyfile")),
            ),
            patch("app.routers.ui.onboarding.get_ssllabs_email", new=AsyncMock(return_value="")),
        ):
            with TestClient(app) as client:
                response = client.get("/onboarding")

        html = response.text
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-current-step="3"', html, "Selecting a mode must advance the wizard to step 3 (access check)")
        # Step 3 panel must be the visible one (not hidden) and carry its preflight form.
        self.assertIn('data-wizard-step data-step="3"', html)
        self.assertNotIn('data-wizard-step data-step="3" hidden inert', html)
        self.assertIn('data-wizard-step data-step="1" hidden inert', html)
        self.assertIn('data-wizard-step data-step="2" hidden inert', html)
        self.assertIn("Run access check", html, "Step 3 access-check form must render its submit button")
        self.assertIn('name="admin_api_url"', html, "Step 3 must render the Admin API field")
        self.assertIn('id="acme-email"', html, "ACME/TLS email field must be present")
        self.assertIn('data-onboarding-preflight-form', html, "Step 3 must mark the preflight form for enhanced loading UX")
        self.assertIn('data-field-check-name="admin_api_url"', html, "Admin API field must render a persisted check chip")
        self.assertIn('data-field-check-status="idle"', html, "Unchecked access-check fields must start in the idle state")
        self.assertRegex(
            html,
            r'id="acme-email"[\s\S]*?\brequired\b',
            "ACME/TLS email field must render as required",
        )

    def test_failed_preflight_keeps_wizard_on_step_two_with_field_error(self) -> None:
        """Regression: an unreachable Admin API must surface on step 2, not jump to step 3."""
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        # Host mode, preflight ran but the Admin API was unreachable -> failed, not passed.
        state = OnboardingWizardState(
            status="failed",
            mode="host",
            runtime_location="host",
            admin_api_url="http://localhost:2019",
            caddyfile_path="/etc/caddy/Caddyfile",
            last_preflight_at="2026-06-13T12:00:00+00:00",
            preflight_passed=False,
            preflight_errors=["Caddy Admin API is not reachable from CaddyBuddy."],
            field_errors={"admin_api_url": ["Caddy Admin API is not reachable from CaddyBuddy."]},
        )

        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.get_onboarding_state", new=AsyncMock(return_value=state)),
            patch(
                "app.routers.ui.onboarding.get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019", caddyfile_path_str="/app/Caddyfile")),
            ),
            patch("app.routers.ui.onboarding.get_ssllabs_email", new=AsyncMock(return_value="")),
        ):
            with TestClient(app) as client:
                response = client.get("/onboarding")

        html = response.text
        self.assertEqual(response.status_code, 200)
        # A failed preflight must NOT advance to the review/execute step (now step 4).
        self.assertIn('data-current-step="3"', html, "Failed preflight must stay on step 3 (access check)")
        self.assertNotIn('data-current-step="4"', html, "Failed preflight must not jump to step 4")
        self.assertNotIn('data-wizard-step data-step="3" hidden inert', html, "Step 3 must be the visible panel")
        # The admin_api_url field error must be rendered on the step-3 form.
        self.assertIn("is-invalid", html, "Admin API field must show the validation error on step 3")
        self.assertIn("Caddy Admin API is not reachable from CaddyBuddy.", html)
        self.assertIn('data-field-check-status="failed"', html, "Failed preflight must persist the failed check chip")

    def _render_onboarding(self, state, current_user=None):
        app = self._build_app()
        current_user = current_user or SimpleNamespace(username="admin", role="admin")
        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.get_onboarding_state", new=AsyncMock(return_value=state)),
            patch(
                "app.routers.ui.onboarding.get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019", caddyfile_path_str="/app/Caddyfile")),
            ),
            patch("app.routers.ui.onboarding.get_ssllabs_email", new=AsyncMock(return_value="")),
        ):
            with TestClient(app) as client:
                return client.get("/onboarding")

    def test_assist_panel_rendered_when_available(self) -> None:
        state = OnboardingWizardState(
            status="failed",
            mode="host",
            runtime_location="host",
            admin_api_url="http://localhost:2019",
            caddyfile_path="/etc/caddy/Caddyfile",
            last_preflight_at="2026-06-13T12:00:00+00:00",
            preflight_passed=False,
            preflight_errors=["Caddy Admin API is not reachable from CaddyBuddy."],
            field_errors={"admin_api_url": ["Caddy Admin API is not reachable from CaddyBuddy."]},
            admin_api_assist_available=True,
        )
        html = self._render_onboarding(state).text
        self.assertIn('name="confirm_admin_api_enablement"', html, "Assist form must render the confirmation checkbox")
        self.assertIn("Enable Admin API and re-check", html)
        self.assertIn("/onboarding/enable-admin-api", html, "Assist form must post to the enable endpoint")

    def test_assist_panel_absent_when_unavailable(self) -> None:
        state = OnboardingWizardState(
            status="failed",
            mode="host",
            runtime_location="host",
            admin_api_url="http://localhost:2019",
            caddyfile_path="/etc/caddy/Caddyfile",
            last_preflight_at="2026-06-13T12:00:00+00:00",
            preflight_passed=False,
            preflight_errors=["Caddy Admin API is not reachable from CaddyBuddy."],
            field_errors={"admin_api_url": ["Caddy Admin API is not reachable from CaddyBuddy."]},
            admin_api_assist_available=False,
        )
        html = self._render_onboarding(state).text
        self.assertNotIn('name="confirm_admin_api_enablement"', html)
        self.assertNotIn("Enable Admin API and re-check", html)

    def test_successful_preflight_renders_persisted_green_checks(self) -> None:
        state = OnboardingWizardState(
            status="in_progress",
            mode="host",
            runtime_location="host",
            admin_api_url="http://localhost:2019",
            caddyfile_path="/app/Caddyfile",
            acme_email="admin@example.com",
            last_preflight_at="2026-06-13T12:00:00+00:00",
            preflight_passed=True,
            field_check_statuses={
                "admin_api_url": "passed",
                "acme_email": "passed",
                "caddyfile_path": "passed",
            },
            field_check_values={
                "admin_api_url": "http://localhost:2019",
                "acme_email": "admin@example.com",
                "caddyfile_path": "/app/Caddyfile",
            },
        )

        html = self._render_onboarding(state).text

        self.assertIn('data-field-check-status="passed"', html)
        self.assertIn('data-field-check-value="http://localhost:2019"', html)
        self.assertIn("Ready", html)

    def test_enable_admin_api_requires_confirmation(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        service = AsyncMock()
        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.validated_form", new=AsyncMock(return_value={})),
            patch("app.routers.ui.onboarding.enable_admin_api_and_reprobe", new=service),
            patch("app.routers.ui.onboarding.push_flash") as push_flash,
        ):
            with TestClient(app) as client:
                response = client.post("/onboarding/enable-admin-api", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        service.assert_not_awaited()
        push_flash.assert_called_once()
        self.assertEqual(push_flash.call_args.args[1], "danger")

    def test_enable_admin_api_success_flash(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        result_state = SimpleNamespace(preflight_passed=True, error_message=None)
        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.validated_form",
                  new=AsyncMock(return_value={"confirm_admin_api_enablement": "yes"})),
            patch("app.routers.ui.onboarding.enable_admin_api_and_reprobe",
                  new=AsyncMock(return_value=result_state)),
            patch("app.routers.ui.onboarding.push_flash") as push_flash,
        ):
            with TestClient(app) as client:
                response = client.post("/onboarding/enable-admin-api", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/onboarding")
        self.assertEqual(push_flash.call_args.args[1], "success")

    def test_enable_admin_api_value_error_flashes_danger(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.validated_form",
                  new=AsyncMock(return_value={"confirm_admin_api_enablement": "yes"})),
            patch("app.routers.ui.onboarding.enable_admin_api_and_reprobe",
                  new=AsyncMock(side_effect=ValueError("Caddy restart capability is not configured."))),
            patch("app.routers.ui.onboarding.push_flash") as push_flash,
        ):
            with TestClient(app) as client:
                response = client.post("/onboarding/enable-admin-api", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(push_flash.call_args.args[1], "danger")
        self.assertIn("restart capability", push_flash.call_args.args[2])

    def test_onboarding_page_prefills_detected_caddyfile_path(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        state = OnboardingWizardState(status="in_progress", runtime_location="host", mode="host")

        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.get_onboarding_state", new=AsyncMock(return_value=state)),
            patch(
                "app.routers.ui.onboarding.get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019", caddyfile_path_str="/app/Caddyfile")),
            ),
            patch("app.routers.ui.onboarding.get_ssllabs_email", new=AsyncMock(return_value="")),
            patch("app.routers.ui.onboarding.suggest_caddyfile_path", return_value="/etc/caddy/Caddyfile"),
            patch(
                "app.routers.ui.onboarding.get_onboarding_caddyfile_path_candidates",
                return_value=("/etc/caddy/Caddyfile", "/usr/local/etc/caddy/Caddyfile"),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/onboarding")

        html = response.text
        self.assertEqual(response.status_code, 200)
        self.assertIn('value="/etc/caddy/Caddyfile"', html)
        self.assertIn("/etc/caddy/Caddyfile", html)
        self.assertIn("/usr/local/etc/caddy/Caddyfile", html)

    def test_onboarding_ui_texts_are_english(self) -> None:
        """Linting rule: all visible onboarding UI text must be in English (no German)."""
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        state = OnboardingWizardState(status="in_progress")

        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.get_onboarding_state", new=AsyncMock(return_value=state)),
            patch(
                "app.routers.ui.onboarding.get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019", caddyfile_path_str="/app/Caddyfile")),
            ),
            patch("app.routers.ui.onboarding.get_ssllabs_email", new=AsyncMock(return_value="")),
        ):
            with TestClient(app) as client:
                response = client.get("/onboarding")

        html = response.text
        GERMAN_FRAGMENTS = [
            "Ausgangssituation",
            "Erkennung und Vorprüfung",
            "Wie läuft Caddy",
            "Zusammenfassung und Bestätigung",
        ]
        for fragment in GERMAN_FRAGMENTS:
            self.assertNotIn(fragment, html, f"German UI text must not appear: {fragment!r}")

    def test_host_summary_uses_capability_based_file_operations_text(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        state = OnboardingWizardState(
            status="in_progress",
            mode="host",
            admin_api_url="http://localhost:2019",
            caddy_version="v2.8.4",
            caddyfile_path="/app/Caddyfile",
            acme_email="admin@example.com",
            last_preflight_at="2026-06-13T12:00:00+00:00",
            preflight_passed=True,
            caddyfile_writable=True,
        )

        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.get_onboarding_state", new=AsyncMock(return_value=state)),
            patch(
                "app.routers.ui.onboarding.get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019", caddyfile_path_str="/app/Caddyfile")),
            ),
            patch("app.routers.ui.onboarding.get_ssllabs_email", new=AsyncMock(return_value="admin@example.com")),
        ):
            with TestClient(app) as client:
                response = client.get("/onboarding")

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-onboarding-wizard', response.text)
        self.assertIn('/static/js/onboarding-wizard.js', response.text)
        self.assertIn('cb-onboarding-wizard__step-button is-inactive', response.text)
        self.assertIn("managed configuration source", response.text)
        self.assertNotIn("Snapshot current Caddyfile, import supported site blocks", response.text)
        self.assertNotIn(">in_progress<", response.text)
        self.assertNotIn("Needs attention", response.text)

    def test_missing_caddy_summary_enables_confirmation_without_preflight_errors(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        state = OnboardingWizardState(
            status="in_progress",
            mode="missing",
            runtime_location="host",
            admin_api_url="http://localhost:2019",
            caddyfile_path="/etc/caddy/Caddyfile",
            acme_email="admin@example.com",
            last_preflight_at="2026-06-13T12:00:00+00:00",
            preflight_passed=True,
            preflight_errors=[],
            preflight_warnings=["Caddy is not installed yet, so the Admin API and version check are skipped."],
        )

        with (
            patch("app.routers.ui.onboarding.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.onboarding.get_onboarding_state", new=AsyncMock(return_value=state)),
            patch(
                "app.routers.ui.onboarding.get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019", caddyfile_path_str="/app/Caddyfile")),
            ),
            patch("app.routers.ui.onboarding.get_ssllabs_email", new=AsyncMock(return_value="admin@example.com")),
        ):
            with TestClient(app) as client:
                response = client.get("/onboarding")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("alert alert-danger", response.text)
        self.assertIn("future active Caddy configuration", response.text)
        self.assertIn("No Admin API call is executed because Caddy is not installed yet.", response.text)
        self.assertIn('data-live-updates-enabled="false"', response.text)
        self.assertNotIn('id="exclusive-manager-confirmed" disabled', response.text)
        self.assertNotIn('data-loading-label="Executing..." disabled', response.text)


if __name__ == "__main__":
    unittest.main()
