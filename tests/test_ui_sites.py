#!/usr/bin/env python3
#
# tests/test_ui_sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config.settings import get_settings


_ENV_OVERRIDES = {
    "CB_SECRET_KEY": "unit-test-secret-key",
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key",
    "CB_ADMIN_PASSWORD": "UnitTestPassword-123A",
    "CADDYBUDDY_ADMIN_PASSWORD": "UnitTestPassword-123A",
}
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}

for key, value in _ENV_OVERRIDES.items():
    os.environ[key] = value

get_settings.cache_clear()

from fastapi.testclient import TestClient
from app.routers.ui.sites import _site_update_requires_deploy
from app.routers.ui.sites import router as sites_router
from tests.ui_test_app import build_ui_test_app


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


class UISitesTests(unittest.TestCase):
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
            sites_router,
            session_override=self._session_override,
            stub_routes=[
                ("GET", "/", "home_page"),
                ("GET", "/caddyfile", "caddyfile_page"),
                ("POST", "/logout", "logout_action"),
            ],
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
        ):
            with TestClient(app) as client:
                response = client.get("/sites/1")

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="site_name"', response.text)
        self.assertIn("Marketing", response.text)
        self.assertIn('name="caddy_directives"', response.text)
        self.assertIn("Site-specific Caddy directives.", response.text)
        self.assertNotIn("Upstream URL", response.text)
        self.assertIn('role="switch"', response.text)
        self.assertIn("site-enabled-label", response.text)
        self.assertIn(">Enabled</label>", response.text)
        self.assertIn(">Site</label>", response.text)
        self.assertIn("data-site-config-form", response.text)
        self.assertIn("data-domain-tag-input", response.text)
        self.assertIn("data-domain-tag-entry", response.text)
        self.assertIn("data-existing-domains=", response.text)
        self.assertRegex(response.text, r"<button[^>]*data-site-save-button[^>]*disabled")
        self.assertRegex(
            response.text,
            r"<button[^>]*data-validate-error-prefix=\"Site configuration invalid\"[^>]*disabled",
        )
        self.assertIn('action="/sites/1/delete"', response.text)
        self.assertIn('aria-label="Delete Marketing"', response.text)
        self.assertNotIn('class="btn btn-outline-danger w-100 js-confirm"', response.text)
        self.assertIn('class="site-row-title"', response.text)
        self.assertIn('class="status-dot site-status-dot status-dot--offline"', response.text)
        self.assertIn('class="site-row-badges"', response.text)
        self.assertIn('class="badge bg-secondary bg-opacity-25 text-body-secondary site-handler-badge">reverse_proxy</span>', response.text)
        self.assertIn('class="site-domain-badges"', response.text)
        self.assertIn('class="badge bg-secondary bg-opacity-25 text-body-secondary site-domain-badge">example.com</span>', response.text)
        self.assertIn('class="panel-card panel-card--table sites-list-panel"', response.text)
        self.assertIn('class="sites-list-scroll"', response.text)
        self.assertIn('class="app-page app-page--sites"', response.text)
        self.assertIn('data-label="Certificate"', response.text)
        self.assertIn('data-sites-certificates-url="/sites/certificates"', response.text)
        self.assertIn('data-site-certificate-domain="example.com"', response.text)
        self.assertIn("Checking certificate...", response.text)
        self.assertIn('class="site-cert__pending"', response.text)
        self.assertIn('class="btn btn-sm btn-outline-primary btn--icon-only"', response.text)
        self.assertIn('aria-label="Edit Marketing"', response.text)

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
        ):
            with TestClient(app) as client:
                response = client.get("/sites")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="badge bg-secondary bg-opacity-25 text-body-secondary site-domain-badge">mail.steiner.rs</span>', response.text)
        self.assertIn('class="badge bg-secondary bg-opacity-25 text-body-secondary site-domain-badge">mail.kwiring.com</span>', response.text)
        self.assertIn('class="status-dot site-status-dot status-dot--online"', response.text)

    def test_sites_page_includes_safe_save_metadata_for_site_name_only_changes(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[])),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value={})),
        ):
            with TestClient(app) as client:
                response = client.get("/sites")

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-loading-label-safe="Creating..."', response.text)
        self.assertIn('data-confirm-safe="Create this site."', response.text)
        self.assertIn('data-confirm-accept-safe="Create"', response.text)

    def test_create_sites_page_renders_validate_and_save_disabled_initially(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[])),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value={})),
        ):
            with TestClient(app) as client:
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
            "example.com": SimpleNamespace(
                exists=True,
                valid=True,
                issued_at=None,
                expires_at=None,
                days_remaining=12,
            )
        }

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[site])),
            patch("app.routers.ui.sites.get_certificate_info_for_domains", new=AsyncMock(return_value=certificate_info)),
        ):
            with TestClient(app) as client:
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
                    }
                }
            },
        )

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
            "example.com": SimpleNamespace(
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
        ):
            with TestClient(app) as client:
                response = client.get("/sites")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="site-cert__summary site-cert__summary--valid"', response.text)
        self.assertIn('class="site-cert__days">74d', response.text)
        self.assertIn('remaining</span>', response.text)
        self.assertIn('class="site-cert__issued">Issued 2026-05-28</div>', response.text)

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
            "example.com": SimpleNamespace(
                exists=False,
                valid=False,
                issued_at=None,
                expires_at=None,
                days_remaining=None,
                error_message="TLS handshake failed: the remote server aborted the connection with an internal TLS error.",
            )
        }

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=[site])),
            patch("app.routers.ui.sites.get_cached_certificate_info_for_domains", new=AsyncMock(return_value=certificate_info)),
        ):
            with TestClient(app) as client:
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
            "caddy_directives": "import security_headers\nimport default_log\n\nreverse_proxy 10.30.0.10:8000",
            "enabled": "true",
        }
        baseline = "(security_headers) {\n\theader X-Frame-Options DENY\n}\n\n(default_log) {\n\tlog\n}"

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_form", new=AsyncMock(return_value=form_data)),
            patch("app.routers.ui.sites.get_baseline_caddyfile", new=AsyncMock(return_value=baseline)),
            patch("app.routers.ui.sites.caddy_service.validate_caddyfile", new=AsyncMock(return_value=(True, "Configuration is valid"))) as validate_mock,
            patch("app.routers.ui.sites.caddy_service.format_site_directives", new=AsyncMock(return_value="import security_headers\nimport default_log\n\nreverse_proxy 10.30.0.10:8000")),
        ):
            with TestClient(app) as client:
                response = client.post("/sites/validate")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["valid"], True)
        self.assertEqual(response.json()["message"], "Site configuration for 'App' is valid.")
        self.assertEqual(
            response.json()["formatted_caddy_directives"],
            "import security_headers\nimport default_log\n\nreverse_proxy 10.30.0.10:8000",
        )

        validate_mock.assert_awaited_once()
        validated_caddyfile = validate_mock.await_args.args[0]
        self.assertIn("(security_headers)", validated_caddyfile)
        self.assertIn("(default_log)", validated_caddyfile)
        self.assertIn("import security_headers", validated_caddyfile)
        self.assertIn("import default_log", validated_caddyfile)
        self.assertIn("example.com {", validated_caddyfile)

    def test_validate_site_rejects_non_admin_user(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="user", role="user")

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_form", new=AsyncMock(return_value={})),
        ):
            with TestClient(app) as client:
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

    def test_renew_certificate_warns_when_no_storage_artifacts_found(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        site = SimpleNamespace(
            id=3,
            site_name="Mail",
            domain="mail.example.com",
            enabled=True,
            caddy_directives="reverse_proxy backend:8080",
        )

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_form", new=AsyncMock(return_value={})),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.get_settings", return_value=SimpleNamespace(caddy_certificates_path=Path("/tmp/certs"))),
            patch("app.routers.ui.sites.caddy_service.purge_certificate_artifacts", new=AsyncMock(return_value=0)) as purge_mock,
            patch("app.routers.ui.sites.sync_caddy_configuration", new=AsyncMock()) as sync_mock,
            patch("app.routers.ui.sites.push_flash") as push_flash_mock,
            patch("app.routers.ui.sites.publish_resource_event", new=AsyncMock()) as event_mock,
        ):
            with TestClient(app) as client:
                response = client.post("/sites/3/renew-certificate", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        purge_mock.assert_awaited_once_with("mail.example.com", Path("/tmp/certs"))
        sync_mock.assert_not_awaited()
        push_flash_mock.assert_called_once()
        self.assertIn("no matching certificate artifacts", push_flash_mock.call_args.args[2])
        self.assertEqual(event_mock.await_count, 2)

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
        sync_result = SimpleNamespace(status="synced", error=None)

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_form", new=AsyncMock(return_value={})),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.get_settings", return_value=SimpleNamespace(caddy_certificates_path=Path("/tmp/certs"))),
            patch("app.routers.ui.sites.caddy_service.purge_certificate_artifacts", new=AsyncMock(return_value=2)) as purge_mock,
            patch("app.routers.ui.sites.invalidate_certificate_cache", new=AsyncMock()) as invalidate_mock,
            patch("app.routers.ui.sites.sync_caddy_configuration", new=AsyncMock(return_value=sync_result)) as sync_mock,
            patch("app.routers.ui.sites.push_flash") as push_flash_mock,
            patch("app.routers.ui.sites.publish_resource_event", new=AsyncMock()) as event_mock,
        ):
            with TestClient(app) as client:
                response = client.post("/sites/3/renew-certificate", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        purge_mock.assert_awaited_once_with("mail.example.com", Path("/tmp/certs"))
        invalidate_mock.assert_awaited_once_with("mail.example.com")
        sync_mock.assert_awaited_once()
        self.assertEqual(sync_mock.await_args.kwargs, {"force": True})
        push_flash_mock.assert_called_once()
        self.assertIn("after removing 2 certificate artifact(s)", push_flash_mock.call_args.args[2])
        self.assertEqual(event_mock.await_count, 2)


if __name__ == "__main__":
    unittest.main()