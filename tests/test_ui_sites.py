#!/usr/bin/env python3
#
# tests/test_ui_sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import re
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from app.config.settings import get_settings
from tests.env_overrides import ModuleEnv

_ENV = ModuleEnv()

from fastapi.testclient import TestClient

from app.routers.ui.sites import (
    _auto_request_certificate_if_missing,
    _site_update_requires_deploy,
)
from app.routers.ui.sites import router as sites_router
from app.services.certificates import CertificateInfo
from tests.ui_test_app import build_ui_test_app


def tearDownModule() -> None:
    _ENV.restore()


class UISitesTests(unittest.TestCase):
    def setUp(self) -> None:
        _ENV.apply()
        self.csrf_patcher = patch("app.middleware.csrf.validate_csrf_token")
        self.mock_validate_csrf = self.csrf_patcher.start()
        self.onboarding_patcher = patch(
            "app.routers.ui._common.get_onboarding_state",
            new=AsyncMock(return_value=SimpleNamespace(status="completed")),
        )
        self.onboarding_patcher.start()

    def tearDown(self) -> None:
        self.onboarding_patcher.stop()
        self.csrf_patcher.stop()
        get_settings.cache_clear()

    @staticmethod
    async def _session_override():
        yield AsyncMock()

    def _build_app(self):
        return build_ui_test_app(
            sites_router,
            session_override=self._session_override,
            stub_routes=[
                ("GET", "/", "home_page"),
                ("GET", "/caddyfile", "caddyfile_page"),
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

    def test_sites_desktop_css_expands_form_column_when_editing(self) -> None:
        css_path = Path(__file__).resolve().parents[1] / "app/static/css/app.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn(
            ".app-page--sites[data-site-form-open]>.app-grid>.sites-form-column {\n        flex: 0 0 auto;\n        width: 58.33333333%;\n    }\n\n    .app-page--sites[data-site-form-open]>.app-grid>.sites-list-column {\n        flex: 0 0 auto;\n        width: 41.66666667%;\n    }",
            css,
            "Desktop Sites page must widen the form column and narrow the table when a site is being edited.",
        )

    def test_sites_stacked_columns_drop_the_grid_gutter_padding(self) -> None:
        css_path = Path(__file__).resolve().parents[1] / "app/static/css/app.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn(
            ".app-page--sites>.app-grid>.sites-form-column,\n    .app-page--sites>.app-grid>.sites-list-column {\n        padding-inline: 0;\n    }",
            css,
            "Below xl the Sites columns stack, so the gutter padding must not inset the panels from the page header.",
        )

    def test_sites_page_renders_config_textarea_and_status_toggle(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=1,
            site_name="Marketing",
            domain="example.com",
            upstream_url="http://backend:8080",
            enabled=False,
            caddy_directives="reverse_proxy backend:8080",
        )

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[site])),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value={})),
            TestClient(app) as client,
        ):
            response = client.get("/sites/1")

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="site_name"', response.text)
        self.assertIn("Marketing", response.text)
        self.assertIn('inputmode="text"', response.text)
        self.assertIn('name="caddy_directives"', response.text)
        self.assertIn("/static/vendor/codemirror/caddybuddy-codemirror.js", response.text)
        self.assertNotIn("Upstream URL", response.text)
        self.assertIn('role="switch"', response.text)
        self.assertIn("site-enabled-label", response.text)
        self.assertIn(">Enabled</label>", response.text)
        self.assertIn(">Site</label>", response.text)
        self.assertIn("data-site-config-form", response.text)
        self.assertIn("data-require-csrf", response.text)
        self.assertIn("data-site-save-button", response.text)
        self.assertIn("data-domain-tag-input", response.text)
        self.assertIn("data-domain-tag-entry", response.text)
        self.assertIn('inputmode="url"', response.text)
        self.assertIn('data-max-domains="25"', response.text)
        self.assertIn('maxlength="262144"', response.text)
        self.assertIn("data-existing-domains=", response.text)
        self.assertIn('class="sites-form-panel__field"', response.text)
        self.assertIn('class="sites-form-panel__config"', response.text)
        self.assertRegex(response.text, r"<button[^>]*data-site-save-button[^>]*disabled")
        self.assertRegex(
            response.text,
            r"<button[^>]*data-validate-error-prefix=\"Site configuration invalid\"[^>]*disabled",
        )
        self.assertIn('action="/sites/1/delete"', response.text)
        self.assertIn('data-csrf-token', response.text)
        self.assertIn('aria-label="Delete Marketing"', response.text)
        self.assertIn('data-confirm="Delete site Marketing (example.com)? The configuration will be removed from Caddy."', response.text)
        self.assertNotIn('class="btn btn-outline-danger w-100 js-confirm"', response.text)
        self.assertIn('class="site-row-title"', response.text)
        self.assertIn('class="status-dot site-status-dot status-dot--offline"', response.text)
        self.assertIn('class="badge site-handler-badge">reverse_proxy</span>', response.text)
        self.assertIn('class="site-domain-badges"', response.text)
        self.assertIn('class="badge site-domain-badge">example.com</span>', response.text)
        self.assertIn('class="panel-card panel-card--table sites-list-panel"', response.text)
        self.assertIn('class="table table--management sites-table align-middle mb-0"', response.text)
        self.assertIn('class="sites-list-scroll"', response.text)
        self.assertIn('class="app-page app-page--sites"', response.text)
        self._assert_response_has_csp_nonce(response)
        self.assertIn('data-label="Certificate"', response.text)
        self.assertIn('data-sites-certificates-url="/sites/certificates"', response.text)
        self.assertIn('data-site-certificate-domain="example.com"', response.text)
        self.assertIn("Checking certificate...", response.text)
        self.assertIn('class="site-cert__pending"', response.text)
        self.assertIn('aria-live="polite" aria-atomic="true"', response.text)
        self.assertIn('class="btn btn-sm btn-outline-secondary btn--icon-only"', response.text)
        self.assertIn('aria-label="Edit Marketing"', response.text)
        self.assertIn('data-site-certificate-domains=\'["example.com"]\'', response.text)

    def test_sites_page_renders_multi_domain_badges_in_sites_table(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=1,
            site_name="Roundcube",
            domain="mail.steiner.rs, mail.kwiring.com",
            upstream_url="http://backend:8080",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[site])),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value={})),
            TestClient(app) as client,
        ):
            response = client.get("/sites")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="badge site-domain-badge">mail.steiner.rs</span>', response.text)
        self.assertIn('class="badge site-domain-badge">mail.kwiring.com</span>', response.text)
        self.assertIn('class="status-dot site-status-dot status-dot--online"', response.text)
        self.assertIn('data-site-certificate-domains=\'["mail.steiner.rs", "mail.kwiring.com"]\'', response.text)

    def test_sites_page_uses_worst_certificate_status_for_multi_domain_sites(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=1,
            site_name="Roundcube",
            domain="mail.steiner.rs, mail.kwiring.com",
            upstream_url="http://backend:8080",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )
        certificate_info = {
            "mail.steiner.rs": CertificateInfo(
                exists=True,
                valid=True,
                status="valid",
                issued_at=datetime(2026, 5, 28, tzinfo=UTC),
                expires_at=None,
                days_remaining=74,
            ),
            "mail.kwiring.com": CertificateInfo(
                exists=True,
                valid=False,
                status="expired",
                issued_at=datetime(2026, 5, 11, tzinfo=UTC),
                expires_at=None,
                days_remaining=0,
            ),
        }

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[site])),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value=certificate_info)),
            TestClient(app) as client,
        ):
            response = client.get("/sites")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="site-cert__summary site-cert__summary--expired"', response.text)
        self.assertIn('<span class="site-cert__status">Expired</span>', response.text)
        self.assertRegex(response.text, r'<div class="site-cert__issued">Issued\s+2026-05-11</div>')
        self.assertNotRegex(response.text, r'<span class="site-cert__status[^>]*aria-label=')

    def test_sites_page_includes_safe_save_metadata_for_site_name_only_changes(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[])),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value={})),
            TestClient(app) as client,
        ):
            response = client.get("/sites")

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-loading-label-safe="Creating..."', response.text)
        self.assertIn('data-confirm-safe="Create this site."', response.text)
        self.assertIn('data-confirm-accept-safe="Create"', response.text)

    def test_sites_page_renders_renewal_metadata_and_disabled_hint(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=1,
            site_name="Mail",
            domain="mail.example.com",
            upstream_url="http://backend:8080",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )
        plan = SimpleNamespace(
            mode="wildcard_scope_required",
            reason="*.example.com",
            requires_confirmation=False,
            scope_name="*.example.com",
            scope_type="wildcard",
            wait_domains=("mail.example.com",),
        )

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[site])),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value={})),
            patch("app.routers.ui.sites.CertificateRenewalService.build_plan", new=AsyncMock(return_value=plan)),
            TestClient(app) as client,
        ):
            response = client.get("/sites")

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-renewal-mode="wildcard_scope_required"', response.text)
        self.assertIn('data-renewal-reason="*.example.com"', response.text)
        self.assertIn('data-renewal-scope="*.example.com"', response.text)
        self.assertIn('data-renewal-scope-type="wildcard"', response.text)
        self.assertIn('data-renewal-wait-domains=\'["mail.example.com"]\'', response.text)
        # Hint is now shown as a title tooltip, not a visible div.
        self.assertIn('title="Wildcard certificate renewal requires the scope *.example.com."', response.text)

    def test_create_sites_page_renders_validate_and_save_disabled_initially(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[])),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value={})),
            TestClient(app) as client,
        ):
            response = client.get("/sites")

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="site_name"', response.text)
        self.assertIn('class="app-page app-page--sites"', response.text)
        self.assertIn("sites-list-column", response.text)
        self.assertIn("sites-form-column", response.text)
        self.assertIn('class="panel-card sites-form-panel"', response.text)
        self.assertIn('class="d-grid gap-3 sites-form-panel__form"', response.text)
        self.assertRegex(response.text, r"<button[^>]*data-site-save-button[^>]*disabled")
        self.assertRegex(
            response.text,
            r"<button[^>]*data-validate-error-prefix=\"Site configuration invalid\"[^>]*disabled",
        )

    def test_sites_certificates_endpoint_returns_primary_domain_data(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=1,
            site_name="Marketing",
            domain="example.com, www.example.com",
            upstream_url="http://backend:8080",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )

        certificate_info = {
            "example.com": CertificateInfo(
                exists=True,
                valid=True,
                status="valid",
                source="local",
                issued_at=None,
                expires_at=None,
                days_remaining=12,
                local_artifact_present=True,
                local_artifact_complete=True,
            )
        }

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[site])),
            patch("app.routers.ui.sites.get_certificate_info_for_domains", new=AsyncMock(return_value=certificate_info)),
            patch.object(get_settings(), "caddy_control_mode", "docker"),
            TestClient(app) as client,
        ):
            response = client.get("/sites/certificates")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "certificates": {
                    "example.com": {
                        "exists": True,
                        "valid": True,
                        "issued_at": None,
                        "expires_at": None,
                        "days_remaining": 12,
                        "error_message": None,
                        "status": "valid",
                        "source": "local",
                        "match_type": None,
                        "is_wildcard": False,
                        "covering_name": None,
                        "checked_at": None,
                        "diagnostics": [],
                        "local_artifact_present": True,
                        "local_artifact_complete": True,
                    }
                },
                "renewals": {
                    "1": {
                        "mode": "artifact_purge",
                        "reason": "standard_renewal",
                        "requires_confirmation": False,
                        "scope_name": "example.com",
                        "scope_type": "domain",
                        "wait_domains": ["example.com", "www.example.com"],
                    }
                }
            },
        )

    def test_sites_page_redirects_to_onboarding_when_wizard_not_completed(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui._common.get_onboarding_state",
                new=AsyncMock(return_value=SimpleNamespace(status="not_started")),
            ),
            TestClient(app) as client,
        ):
            response = client.get("/sites", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/onboarding")

    def test_sites_page_redirects_non_admin_user(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="user", role="user")

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            TestClient(app) as client,
        ):
            response = client.get("/sites", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")

    def test_sites_certificates_rejects_non_admin_user(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="user", role="user")

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            TestClient(app) as client,
        ):
            response = client.get("/sites/certificates")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Administrator access is required."})

    def test_sites_page_renders_compact_certificate_summary(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=1,
            site_name="Marketing",
            domain="example.com, www.example.com",
            upstream_url="http://backend:8080",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )
        issued_at = datetime(2026, 5, 28, tzinfo=UTC)
        certificate_info = {
            "example.com": CertificateInfo(
                exists=True,
                valid=True,
                issued_at=issued_at,
                expires_at=None,
                days_remaining=74,
            )
        }

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[site])),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value=certificate_info)),
            TestClient(app) as client,
        ):
            response = client.get("/sites")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="site-cert__summary site-cert__summary--valid"', response.text)
        self.assertRegex(response.text, r'<div class="site-cert__issued">Issued\s+2026-05-28</div>')
        # A hover-only hint is unreachable on touch devices, so the date must not
        # hide in a title or tooltip.
        self.assertNotIn('title="Issued', response.text)
        self.assertNotIn("has-tooltip", response.text)

    def test_sites_page_renders_issued_date_from_local_certificate_storage(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=1,
            site_name="DAV",
            domain="dav.cirrio.de",
            upstream_url="http://backend:8080",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )
        issued_at = datetime(2026, 5, 11, tzinfo=UTC)
        certificate_info = {
            "dav.cirrio.de": CertificateInfo(
                exists=True,
                valid=True,
                issued_at=issued_at,
                expires_at=None,
                days_remaining=68,
            )
        }

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[site])),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value=certificate_info)),
            TestClient(app) as client,
        ):
            response = client.get("/sites")

        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.text, r'<div class="site-cert__issued">Issued\s+2026-05-11</div>')
        self.assertRegex(response.text, r'class="visually-hidden">68 days\s+remaining</span>')
        self.assertNotRegex(response.text, r'<span class="site-cert__days[^>]*aria-label=')

    def test_sites_page_renders_certificate_fetch_error_message(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=1,
            site_name="Marketing",
            domain="example.com",
            upstream_url="http://backend:8080",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )
        certificate_info = {
            "example.com": CertificateInfo(
                exists=False,
                valid=False,
                issued_at=None,
                expires_at=None,
                days_remaining=None,
                error_message="TLS handshake failed: the remote server aborted the connection with an internal TLS error.",
                status="error",
            )
        }

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[site])),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value=certificate_info)),
            TestClient(app) as client,
        ):
            response = client.get("/sites")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="site-cert__error"', response.text)
        self.assertIn("Certificate check failed", response.text)
        self.assertIn("internal TLS error", response.text)

    def test_validate_site_uses_baseline_for_import_snippets(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        form_data = {
            "site_name": "App",
            "domain": "example.com",
            "caddy_directives": "import security_headers\n\nreverse_proxy 10.30.0.10:8000",
            "enabled": "true",
        }
        baseline = "(security_headers) {\n\theader X-Frame-Options DENY\n}"
        rendered_caddyfile = (
            f"{baseline}\n\n"
            "example.com {\n"
            "    import security_headers\n"
            "    reverse_proxy 10.30.0.10:8000\n"
            "}"
        )

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_csrf_form", new=AsyncMock(return_value=form_data)),
            patch(
                "app.routers.ui.sites.build_site_validation_caddyfile",
                new=AsyncMock(return_value=rendered_caddyfile),
            ) as build_mock,
            patch("app.routers.ui.sites.caddy_service.validate_caddyfile", new=AsyncMock(return_value=(True, "Configuration is valid"))) as validate_mock,
            patch("app.routers.ui.sites.caddy_service.format_site_directives", new=AsyncMock(return_value="import security_headers\n\nreverse_proxy 10.30.0.10:8000")),
            patch("app.routers.ui.sites.get_caddy_config", new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019"))),
            TestClient(app) as client,
        ):
            response = client.post("/sites/validate")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["valid"], True)
        self.assertEqual(response.json()["message"], "Site configuration for 'App' is valid.")
        self.assertEqual(
            response.json()["formatted_caddy_directives"],
            "import security_headers\n\nreverse_proxy 10.30.0.10:8000",
        )

        build_mock.assert_awaited_once()
        self.assertEqual(build_mock.await_args.kwargs["domain"], "example.com")
        self.assertEqual(
            build_mock.await_args.kwargs["caddy_directives"],
            "import security_headers\n\nreverse_proxy 10.30.0.10:8000",
        )
        validate_mock.assert_awaited_once()
        validated_caddyfile = validate_mock.await_args.args[0]
        self.assertIn("(security_headers)", validated_caddyfile)
        self.assertIn("import security_headers", validated_caddyfile)
        self.assertNotIn("import default_log", validated_caddyfile)
        self.assertIn("example.com {", validated_caddyfile)

    def test_validate_site_rejects_non_admin_user(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="user", role="user")

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_csrf_form", new=AsyncMock(return_value={})),
            TestClient(app) as client,
        ):
            response = client.post("/sites/validate")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Administrator access is required."})

    def test_site_update_requires_deploy_ignores_site_name_only_changes(self) -> None:
        site = SimpleNamespace(
            site_name="Old name",
            domain="example.com",
            caddy_directives="reverse_proxy backend:8080",
            enabled=True,
        )

        self.assertFalse(
            _site_update_requires_deploy(
                site,
                domain="example.com",
                caddy_directives="reverse_proxy backend:8080",
                enabled=True,
            )
        )
        self.assertTrue(
            _site_update_requires_deploy(
                site,
                domain="www.example.com",
                caddy_directives="reverse_proxy backend:8080",
                enabled=True,
            )
        )

    def test_auto_request_certificate_stops_on_storage_error(self) -> None:
        session = AsyncMock()

        with (
            patch("app.routers.ui.sites.get_settings", return_value=SimpleNamespace(caddy_certificates_path=Path("/certificates"))),
            patch("app.routers.ui.sites._has_local_certificate", new=AsyncMock(return_value=(False, True))),
            patch("app.routers.ui.sites.sync_caddy_configuration", new=AsyncMock()) as sync_mock,
        ):
            requested, error = asyncio.run(
                _auto_request_certificate_if_missing(session, "mail.example.com")
            )

        self.assertFalse(requested)
        self.assertEqual(error, "Certificate storage could not be read while checking local artifacts.")
        sync_mock.assert_not_awaited()

    def test_renew_certificate_requests_certificate_when_no_local_artifacts_exist(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=3,
            site_name="Mail",
            domain="mail.example.com",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )
        plan = SimpleNamespace(
            mode="acquisition_sync",
            reason="local_artifact_missing",
            requires_confirmation=False,
            scope_name=None,
            scope_type="domain",
            wait_domains=("mail.example.com",),
        )

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_csrf_form", new=AsyncMock(return_value={})),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.sync_caddy_configuration", new=AsyncMock(return_value=SimpleNamespace(status="synced", error=None))),
            patch("app.routers.ui.sites.CertificateRenewalService.build_plan", new=AsyncMock(return_value=plan)) as build_mock,
            patch("app.routers.ui.sites.CertificateRenewalService.execute", new=AsyncMock(return_value=(True, "No certificate artifacts were found on disk"))) as execute_mock,
            patch("app.routers.ui.sites.push_flash") as push_flash_mock,
            patch("app.routers.ui.sites.publish_resource_event", new=AsyncMock()) as event_mock,
            TestClient(app) as client,
        ):
            response = client.post("/sites/3/renew-certificate", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        build_mock.assert_awaited_once_with(site)
        execute_mock.assert_awaited_once_with(site, plan, confirmed=False, progress=ANY)
        push_flash_mock.assert_called_once()
        self.assertIn("No certificate artifacts were found on disk", push_flash_mock.call_args.args[2])
        self.assertEqual(event_mock.await_count, 2)
        event_mock.assert_any_await("certificate", "renewing", "mail.example.com", {"site_id": 3, "scope": "mail.example.com", "domain": "mail.example.com"})
        event_mock.assert_any_await("certificate", "renewal_sync_only", "mail.example.com", {"site_id": 3, "scope": "mail.example.com", "domain": "mail.example.com", "message": "No certificate artifacts were found on disk"})

    def test_renew_certificate_aborts_on_wildcard_covering_cert(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=3,
            site_name="Mail",
            domain="mail.example.com",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )
        plan = SimpleNamespace(
            mode="wildcard_scope_required",
            reason="*.example.com",
            requires_confirmation=False,
            scope_name="*.example.com",
        )

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_csrf_form", new=AsyncMock(return_value={})),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.CertificateRenewalService.build_plan", new=AsyncMock(return_value=plan)) as build_mock,
            patch("app.routers.ui.sites.push_flash") as push_flash_mock,
            patch("app.routers.ui.sites.publish_resource_event", new=AsyncMock()) as event_mock,
            TestClient(app) as client,
        ):
            response = client.post("/sites/3/renew-certificate", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        build_mock.assert_awaited_once_with(site)
        push_flash_mock.assert_called_once()
        self.assertIn("requires the wildcard scope", push_flash_mock.call_args.args[2])
        self.assertEqual(event_mock.await_count, 2)
        event_mock.assert_any_await("certificate", "wildcard_scope_required", "mail.example.com", {"site_id": 3, "scope": "*.example.com", "domain": "mail.example.com"})
        event_mock.assert_any_await("certificate", "renewal_failed", "mail.example.com", {"site_id": 3, "scope": "*.example.com", "domain": "mail.example.com", "error": "Wildcard scope renewal is required for *.example.com."})

    def test_restart_repair_without_confirmation_does_not_publish_renewing(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=3,
            site_name="Mail",
            domain="mail.example.com",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )
        plan = SimpleNamespace(
            mode="restart_repair",
            reason="local_artifact_missing",
            requires_confirmation=True,
            scope_name="mail.example.com",
            scope_type="domain",
            wait_domains=("mail.example.com",),
        )

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_csrf_form", new=AsyncMock(return_value={})),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.CertificateRenewalService.build_plan", new=AsyncMock(return_value=plan)),
            patch("app.routers.ui.sites.CertificateRenewalService.execute", new=AsyncMock()) as execute_mock,
            patch("app.routers.ui.sites.sync_caddy_configuration", new=AsyncMock()) as sync_mock,
            patch("app.routers.ui.sites.push_flash") as push_flash_mock,
            patch("app.routers.ui.sites.publish_resource_event", new=AsyncMock()) as event_mock,
            TestClient(app) as client,
        ):
            response = client.post("/sites/3/renew-certificate", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        push_flash_mock.assert_called_once_with(unittest.mock.ANY, "info", "Confirmation required to proceed with restart.")
        execute_mock.assert_not_awaited()
        sync_mock.assert_not_awaited()
        self.assertEqual(event_mock.await_count, 0)

    def test_create_site_auto_requests_certificate_when_local_cert_is_missing(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=5,
            site_name="DAV",
            domain="dav.cirrio.de",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )
        form_data = {
            "site_name": "DAV",
            "domain": "dav.cirrio.de",
            "caddy_directives": "reverse_proxy backend:8080",
            "enabled": "true",
        }

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_csrf_form", new=AsyncMock(return_value=form_data)),
            patch("app.routers.ui.sites.site_repository.domain_exists", new=AsyncMock(return_value=False)),
            patch("app.routers.ui.sites.site_repository.create", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.validate_and_deploy_full_caddyfile", new=AsyncMock(return_value=(True, "ok"))),
            patch("app.routers.ui.sites._auto_request_certificate_if_missing", new=AsyncMock(return_value=(True, None))) as auto_request_mock,
            patch("app.routers.ui.sites.push_flash") as push_flash_mock,
            patch("app.routers.ui.sites.publish_resource_event", new=AsyncMock()) as event_mock,
            TestClient(app) as client,
        ):
            response = client.post("/sites", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        auto_request_mock.assert_awaited_once_with(unittest.mock.ANY, "dav.cirrio.de")
        self.assertEqual(push_flash_mock.call_count, 2)
        self.assertIn("Automatic certificate request triggered", push_flash_mock.call_args_list[0].args[2])
        self.assertIn("created and deployed", push_flash_mock.call_args_list[1].args[2])
        self.assertEqual(event_mock.await_count, 1)

    def _render_sites_list(self, *sites):
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=list(sites))),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value={})),
            TestClient(app) as client,
        ):
            return client.get("/sites")

    def test_sites_page_renders_play_button_for_running_site(self) -> None:
        site = SimpleNamespace(
            id=3, site_name="Shop", domain="shop.example.com", upstream_url="http://shop:80",
            enabled=True, maintenance_mode=False, caddy_directives="reverse_proxy shop:80",
        )

        response = self._render_sites_list(site)

        self.assertEqual(response.status_code, 200)
        self.assertIn('action="/sites/3/maintenance"', response.text)
        self.assertRegex(response.text, r'<input type="hidden" name="action"\s+value="stop">')
        self.assertIn('data-site-run-toggle="running"', response.text)
        self.assertIn('aria-label="Stop Shop"', response.text)
        self.assertIn('data-confirm-accept="Stop site"', response.text)
        self.assertNotIn("site-maintenance-badge", response.text)

    def test_sites_page_renders_stop_button_and_badge_for_stopped_site(self) -> None:
        site = SimpleNamespace(
            id=4, site_name="Blog", domain="blog.example.com", upstream_url="http://blog:80",
            enabled=True, maintenance_mode=True, caddy_directives="reverse_proxy blog:80",
        )

        response = self._render_sites_list(site)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-site-run-toggle="stopped"', response.text)
        self.assertRegex(response.text, r'<input type="hidden" name="action"\s+value="start">')
        self.assertIn('aria-label="Start Blog"', response.text)
        self.assertIn('class="badge site-maintenance-badge">Maintenance</span>', response.text)
        self.assertIn("status-dot site-status-dot status-dot--maintenance", response.text)
        self.assertRegex(response.text, r'<button[^>]*data-site-run-toggle="stopped"[^>]*>')
        self.assertNotRegex(response.text, r'<button[^>]*data-site-run-toggle="stopped"[^>]*js-confirm')

    def test_sites_page_disables_run_toggle_for_disabled_site(self) -> None:
        site = SimpleNamespace(
            id=5, site_name="Old", domain="old.example.com", upstream_url="http://old:80",
            enabled=False, maintenance_mode=True, caddy_directives="reverse_proxy old:80",
        )

        response = self._render_sites_list(site)

        self.assertRegex(response.text, r'<button[^>]*data-site-run-toggle="stopped"[^>]*disabled\s+aria-disabled="true"')
        self.assertNotIn("site-maintenance-badge", response.text)

    def _post_maintenance(self, site, form_data, *, deploy_result=(True, "ok")):
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        session = AsyncMock()

        async def session_override():
            yield session

        from app.database.session import get_db_session

        app.dependency_overrides[get_db_session] = session_override
        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_csrf_form", new=AsyncMock(return_value=form_data)),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.site_repository.set_maintenance_mode", new=AsyncMock()) as set_mode,
            patch(
                "app.routers.ui.sites.validate_and_deploy_full_caddyfile",
                new=AsyncMock(return_value=deploy_result),
            ) as deploy,
            patch("app.routers.ui.sites.push_flash") as push_flash_mock,
            patch("app.routers.ui.sites.publish_resource_event", new=AsyncMock()) as event_mock,
            TestClient(app) as client,
        ):
            response = client.post(f"/sites/{site.id}/maintenance", follow_redirects=False)
        return response, session, set_mode, deploy, push_flash_mock, event_mock

    def test_stop_site_enables_maintenance_mode_and_deploys(self) -> None:
        site = SimpleNamespace(id=7, site_name="Shop", enabled=True, maintenance_mode=False)

        response, session, set_mode, deploy, push_flash_mock, event_mock = self._post_maintenance(
            site, {"action": "stop"},
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        set_mode.assert_awaited_once_with(ANY, site, True)
        deploy.assert_awaited_once()
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
        self.assertIn("stopped", push_flash_mock.call_args.args[2])
        event_mock.assert_awaited_once_with("site", "updated", "7")

    def test_start_site_rolls_back_when_deploy_fails(self) -> None:
        site = SimpleNamespace(id=8, site_name="Blog", enabled=True, maintenance_mode=True)

        response, session, set_mode, _deploy, push_flash_mock, event_mock = self._post_maintenance(
            site, {"action": "start"}, deploy_result=(False, "Caddy Admin API unavailable."),
        )

        self.assertEqual(response.status_code, 303)
        set_mode.assert_awaited_once_with(ANY, site, False)
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()
        self.assertEqual(push_flash_mock.call_args.args[1], "danger")
        self.assertIn("was not started: Caddy Admin API unavailable.", push_flash_mock.call_args.args[2])
        event_mock.assert_not_awaited()

    def test_maintenance_toggle_rejects_disabled_site(self) -> None:
        site = SimpleNamespace(id=10, site_name="Old", enabled=False, maintenance_mode=False)

        response, session, set_mode, deploy, flash, event = self._post_maintenance(site, {"action": "stop"})

        self.assertEqual(response.status_code, 303)
        set_mode.assert_not_awaited()
        deploy.assert_not_awaited()
        session.commit.assert_not_awaited()
        event.assert_not_awaited()
        self.assertEqual(flash.call_args.args[1], "warning")

    def test_maintenance_toggle_is_idempotent_and_rejects_unknown_actions(self) -> None:
        site = SimpleNamespace(id=9, site_name="Shop", enabled=True, maintenance_mode=True)

        _response, _session, set_mode, deploy, _flash, _event = self._post_maintenance(site, {"action": "stop"})
        set_mode.assert_not_awaited()
        deploy.assert_not_awaited()

        _response, _session, set_mode, deploy, flash, _event = self._post_maintenance(site, {"action": "toggle"})
        set_mode.assert_not_awaited()
        deploy.assert_not_awaited()
        self.assertEqual(flash.call_args.args[1:], ("danger", "Invalid site action."))

    def test_renew_certificate_purges_artifacts_and_forces_sync(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=3,
            site_name="Mail",
            domain="mail.example.com",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )
        plan = SimpleNamespace(
            mode="artifact_purge",
            reason="standard_renewal",
            requires_confirmation=False,
            scope_name=None,
            scope_type="domain",
            wait_domains=("mail.example.com",),
        )

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_csrf_form", new=AsyncMock(return_value={})),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.sync_caddy_configuration", new=AsyncMock(return_value=SimpleNamespace(status="synced", error=None))),
            patch("app.routers.ui.sites.CertificateRenewalService.build_plan", new=AsyncMock(return_value=plan)) as build_mock,
            patch("app.routers.ui.sites.CertificateRenewalService.execute", new=AsyncMock(return_value=(True, "removed 2 artifact(s)"))) as execute_mock,
            patch("app.routers.ui.sites.push_flash") as push_flash_mock,
            patch("app.routers.ui.sites.publish_resource_event", new=AsyncMock()) as event_mock,
            TestClient(app) as client,
        ):
            response = client.post("/sites/3/renew-certificate", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        build_mock.assert_awaited_once_with(site)
        execute_mock.assert_awaited_once_with(site, plan, confirmed=False, progress=ANY)
        push_flash_mock.assert_called_once()
        self.assertIn("removed 2 artifact(s)", push_flash_mock.call_args.args[2])
        self.assertEqual(event_mock.await_count, 2)
        event_mock.assert_any_await("certificate", "renewing", "mail.example.com", {"site_id": 3, "scope": "mail.example.com", "domain": "mail.example.com"})
        event_mock.assert_any_await("certificate", "renewed", "mail.example.com", {"site_id": 3, "scope": "mail.example.com", "domain": "mail.example.com", "message": "removed 2 artifact(s)"})


if __name__ == "__main__":
    unittest.main()
