#!/usr/bin/env python3
#
# tests/test_ui_sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import ssl
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from app.routers.ui import sites


def _build_request(path: str = "/sites") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "session": {},
            "client": ("127.0.0.1", 12345),
        }
    )


class UiSitesTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_not_after_from_der_certificate_uses_decoded_certificate(self) -> None:
        with (
            patch("app.routers.ui.sites.ssl.DER_cert_to_PEM_cert", return_value="PEM"),
            patch(
                "app.routers.ui.sites.ssl._ssl._test_decode_cert",
                return_value={"notAfter": "Aug  6 13:24:34 2026 GMT"},
            ),
        ):
            result = await sites._extract_not_after_from_der_certificate(b"der-cert")

        self.assertEqual(result, "Aug  6 13:24:34 2026 GMT")

    async def test_fetch_site_certificate_expiry_skips_ip_literal_domains(self) -> None:
        with patch("app.routers.ui.sites.asyncio.open_connection", new=AsyncMock()) as open_connection:
            result = await sites._fetch_site_certificate_expiry("127.0.0.1")

        open_connection.assert_not_awaited()
        self.assertIsNone(result)

    async def test_fetch_site_certificate_expiry_falls_back_to_binary_certificate_on_verification_error(self) -> None:
        writer = SimpleNamespace(
            get_extra_info=lambda key: SimpleNamespace(
                getpeercert=lambda binary_form=False: b"der-cert" if binary_form else {}
            ),
            close=lambda: None,
            wait_closed=AsyncMock(),
        )

        with (
            patch(
                "app.routers.ui.sites.asyncio.open_connection",
                new=AsyncMock(
                    side_effect=[
                        ssl.SSLCertVerificationError("verify failed"),
                        (object(), writer),
                    ]
                ),
            ) as open_connection,
            patch(
                "app.routers.ui.sites._extract_not_after_from_der_certificate",
                new=AsyncMock(return_value="Aug  6 13:24:34 2026 GMT"),
            ) as extract_not_after,
        ):
            result = await sites._fetch_site_certificate_expiry("secure.example.com")

        self.assertEqual(result, datetime(2026, 8, 6, 13, 24, 34, tzinfo=UTC))
        self.assertEqual(open_connection.await_count, 2)
        extract_not_after.assert_called_once_with(b"der-cert")

    async def test_load_site_certificate_expiries_only_probes_ssl_enabled_sites(self) -> None:
        ssl_site = SimpleNamespace(id=1, domain="secure.example.com", ssl_enabled=True)
        plain_site = SimpleNamespace(id=2, domain="plain.example.com", ssl_enabled=False)
        expires_at = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

        with patch(
            "app.routers.ui.sites._fetch_site_certificate_expiry",
            new=AsyncMock(return_value=expires_at),
        ) as fetch_expiry:
            result = await sites._load_site_certificate_expiries([ssl_site, plain_site])

        fetch_expiry.assert_awaited_once_with("secure.example.com")
        self.assertEqual(result, {1: expires_at})

    async def test_load_site_certificate_expiries_limits_probe_concurrency(self) -> None:
        sites_to_probe = [
            SimpleNamespace(id=1, domain="one.example.com", ssl_enabled=True),
            SimpleNamespace(id=2, domain="two.example.com", ssl_enabled=True),
            SimpleNamespace(id=3, domain="three.example.com", ssl_enabled=True),
            SimpleNamespace(id=4, domain="four.example.com", ssl_enabled=True),
            SimpleNamespace(id=5, domain="five.example.com", ssl_enabled=True),
            SimpleNamespace(id=6, domain="six.example.com", ssl_enabled=True),
        ]
        active = 0
        max_active = 0

        async def _fake_fetch(_domain: str) -> datetime | None:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return None

        with patch("app.routers.ui.sites._fetch_site_certificate_expiry", new=AsyncMock(side_effect=_fake_fetch)):
            result = await sites._load_site_certificate_expiries(sites_to_probe)

        self.assertEqual(set(result.keys()), {1, 2, 3, 4, 5, 6})
        self.assertLessEqual(max_active, sites._TLS_PROBE_CONCURRENCY)

    async def test_sites_page_includes_certificate_expiries_in_template_context(self) -> None:
        request = _build_request()
        session = object()
        current_user = SimpleNamespace(id=1, role="admin")
        listed_sites = [SimpleNamespace(id=1, domain="secure.example.com", ssl_enabled=True, config_template=None)]
        expires_at = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.site_repository.list_all", new=AsyncMock(return_value=listed_sites)),
            patch("app.routers.ui.sites.config_template_repository.list_all", new=AsyncMock(return_value=[])),
            patch("app.routers.ui.sites.server_repository.list_all", new=AsyncMock(return_value=[])),
            patch(
                "app.routers.ui.sites._load_site_certificate_expiries",
                new=AsyncMock(return_value={1: expires_at}),
            ) as load_expiries,
            patch("app.routers.ui.sites.render_template", return_value=SimpleNamespace(status_code=200)) as render_template,
        ):
            response = await sites.sites_page(request, session=session)

        load_expiries.assert_awaited_once_with(listed_sites)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(render_template.call_args.kwargs["context"]["site_certificate_expiries"], {1: expires_at})

    async def test_save_site_rejects_invalid_domain_format_before_db_writes(self) -> None:
        request = _build_request()
        session = object()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.sites.validated_form",
                new=AsyncMock(
                    return_value={
                        "domain": "not a domain!!!",
                        "config_template_id": "1",
                        "ssl_provider": "letsencrypt",
                    }
                ),
            ),
            patch("app.routers.ui.sites.config_template_repository.get_by_id", new=AsyncMock()) as get_template,
            patch("app.routers.ui.sites.site_repository.create", new=AsyncMock()) as create_site,
        ):
            response = await sites.save_site(request, session=session)

        get_template.assert_not_awaited()
        create_site.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        self.assertEqual(
            request.session["flashes"],
            [{"category": "danger", "message": "Enter a valid domain name."}],
        )

    async def test_save_site_rejects_ip_literal_domain_before_db_writes(self) -> None:
        request = _build_request()
        session = object()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.sites.validated_form",
                new=AsyncMock(
                    return_value={
                        "domain": "169.254.169.254",
                        "config_template_id": "1",
                        "ssl_provider": "letsencrypt",
                    }
                ),
            ),
            patch("app.routers.ui.sites.config_template_repository.get_by_id", new=AsyncMock()) as get_template,
        ):
            response = await sites.save_site(request, session=session)

        get_template.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        self.assertEqual(
            request.session["flashes"],
            [{"category": "danger", "message": "Enter a valid domain name."}],
        )

    async def test_save_site_rejects_invalid_ssl_provider(self) -> None:
        request = _build_request()
        session = object()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.sites.validated_form",
                new=AsyncMock(
                    return_value={
                        "domain": "secure.example.com",
                        "config_template_id": "1",
                        "ssl_provider": "bogus",
                    }
                ),
            ),
            patch("app.routers.ui.sites.config_template_repository.get_by_id", new=AsyncMock()) as get_template,
        ):
            response = await sites.save_site(request, session=session)

        get_template.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        self.assertEqual(
            request.session["flashes"],
            [{"category": "danger", "message": "Invalid SSL provider selected."}],
        )

    async def test_save_site_publishes_created_event(self) -> None:
        request = _build_request()
        session = object()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        template = SimpleNamespace(id=3, caddyfile="respond ok", variables={})
        site = SimpleNamespace(id=9, domain="secure.example.com")

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.sites.validated_form",
                new=AsyncMock(
                    return_value={
                        "domain": "secure.example.com",
                        "config_template_id": "3",
                        "ssl_provider": "letsencrypt",
                        "enabled": "on",
                        "ssl_enabled": "on",
                    }
                ),
            ),
            patch("app.routers.ui.sites.config_template_repository.get_by_id", new=AsyncMock(return_value=template)),
            patch("app.routers.ui.sites.site_repository.domain_exists", new=AsyncMock(return_value=False)),
            patch("app.routers.ui.sites.site_repository.create", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.audit_commit_and_flash", new=AsyncMock()) as audit_commit,
            patch("app.routers.ui.sites.publish_resource_event", new=AsyncMock()) as publish_resource_event,
        ):
            response = await sites.save_site(request, session=session)

        audit_commit.assert_awaited_once()
        publish_resource_event.assert_awaited_once_with("site", "created", "9")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites/9")

    async def test_save_site_rejects_missing_upstream_for_required_template(self) -> None:
        request = _build_request()
        session = object()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        template = SimpleNamespace(id=3, caddyfile="reverse_proxy {{upstream}}", variables={})

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.sites.validated_form",
                new=AsyncMock(
                    return_value={
                        "domain": "secure.example.com",
                        "config_template_id": "3",
                        "ssl_provider": "letsencrypt",
                        "enabled": "on",
                        "ssl_enabled": "on",
                    }
                ),
            ),
            patch("app.routers.ui.sites.config_template_repository.get_by_id", new=AsyncMock(return_value=template)),
            patch("app.routers.ui.sites.site_repository.create", new=AsyncMock()) as create_site,
        ):
            response = await sites.save_site(request, session=session)

        create_site.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        self.assertEqual(
            request.session["flashes"],
            [{"category": "danger", "message": "This Caddyfile requires an upstream target."}],
        )

    async def test_deploy_site_propagates_unexpected_exceptions(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/sites/9/deploy",
                "headers": [],
                "query_string": b"",
                "session": {},
                "client": ("127.0.0.1", 12345),
            }
        )
        session = SimpleNamespace(rollback=AsyncMock())
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        site = SimpleNamespace(id=9, domain="secure.example.com")
        server = SimpleNamespace(id=7, name="edge-1")

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_form", new=AsyncMock(return_value={"server_id": "7"})),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.server_repository.get_by_id", new=AsyncMock(return_value=server)),
            patch(
                "app.routers.ui.sites.deployment_engine.deploy",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                await sites.deploy_site(request, site_id=9, session=session)

        session.rollback.assert_not_awaited()

    async def test_deploy_site_flashes_actionable_message_for_missing_upstream(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/sites/9/deploy",
                "headers": [],
                "query_string": b"",
                "session": {},
                "client": ("127.0.0.1", 12345),
            }
        )
        session = SimpleNamespace(rollback=AsyncMock())
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        site = SimpleNamespace(id=9, domain="secure.example.com")
        server = SimpleNamespace(id=7, name="edge-1")

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_form", new=AsyncMock(return_value={"server_id": "7"})),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.server_repository.get_by_id", new=AsyncMock(return_value=server)),
            patch(
                "app.routers.ui.sites.deployment_engine.deploy",
                new=AsyncMock(side_effect=sites.DeploymentError("Configuration rendering failed: upstream")),
            ),
        ):
            response = await sites.deploy_site(request, site_id=9, session=session)

        session.rollback.assert_awaited_once()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites/9")
        self.assertEqual(
            request.session["flashes"],
            [{"category": "danger", "message": "Deployment error: This Caddyfile requires an upstream target."}],
        )

    async def test_deploy_site_returns_navigation_page_on_success(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/sites/9/deploy",
                "headers": [],
                "query_string": b"",
                "session": {},
                "client": ("127.0.0.1", 12345),
            }
        )
        session = SimpleNamespace()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        site = SimpleNamespace(id=9, domain="secure.example.com")
        server = SimpleNamespace(id=7, name="edge-1")
        result = SimpleNamespace(success=True)

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_form", new=AsyncMock(return_value={"server_id": "7"})),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
            patch("app.routers.ui.sites.server_repository.get_by_id", new=AsyncMock(return_value=server)),
            patch("app.routers.ui.sites.deployment_engine.deploy", new=AsyncMock(return_value=result)),
            patch("app.routers.ui.sites.audit_commit_and_flash", new=AsyncMock()) as audit_commit,
            patch("app.routers.ui.sites.publish_resource_event", new=AsyncMock()) as publish_resource_event,
        ):
            response = await sites.deploy_site(request, site_id=9, session=session)

        audit_commit.assert_awaited_once()
        publish_resource_event.assert_awaited_once_with("site", "updated", "9")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.body.decode("utf-8")
        self.assertIn('http-equiv="refresh" content="0;url=/sites/9"', body)
        self.assertIn('window.location.replace("/sites/9")', body)

    def test_deployment_navigation_response_normalizes_non_local_target(self) -> None:
        response = sites._deployment_navigation_response("javascript:alert(1)")

        self.assertEqual(response.status_code, 200)
        body = response.body.decode("utf-8")
        self.assertIn('http-equiv="refresh" content="0;url=/"', body)
        self.assertIn('window.location.replace("/")', body)
        self.assertNotIn("javascript:alert(1)", body)

    async def test_delete_site_rolls_back_on_integrity_error(self) -> None:
        request = _build_request("/sites/9/delete")
        session = SimpleNamespace(rollback=AsyncMock())
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        site = SimpleNamespace(id=9, domain="secure.example.com")

        with (
            patch("app.routers.ui.sites.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.sites.validated_form", new=AsyncMock(return_value={})),
            patch("app.routers.ui.sites.site_repository.get_by_id", new=AsyncMock(return_value=site)),
            patch(
                "app.routers.ui.sites.site_repository.delete",
                new=AsyncMock(side_effect=IntegrityError("stmt", "params", Exception("fk"))),
            ),
        ):
            response = await sites.delete_site(request, site_id=9, session=session)

        session.rollback.assert_awaited_once()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/sites")
        self.assertEqual(
            request.session["flashes"],
            [{
                "category": "danger",
                "message": "Cannot delete site: existing deployments or related records must be removed first.",
            }],
        )

    async def test_preview_site_rejects_invalid_ssl_provider(self) -> None:
        request = _build_request("/sites/preview")
        session = object()
        current_user = SimpleNamespace(id=1, role="user")

        with (
            patch("app.routers.ui.sites.require_user", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.sites.validated_form",
                new=AsyncMock(
                    return_value={
                        "config_template_id": "3",
                        "domain": "secure.example.com",
                        "ssl_provider": "bogus",
                    }
                ),
            ),
        ):
            response = await sites.preview_site(request, session=session)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.body, b'{"preview":"# Invalid SSL provider","errors":["Invalid SSL provider selected"]}')


if __name__ == "__main__":
    unittest.main()