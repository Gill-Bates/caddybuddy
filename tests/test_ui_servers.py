#!/usr/bin/env python3
#
# tests/test_ui_servers.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.requests import Request
from sqlalchemy.exc import IntegrityError

from app.routers.ui import servers


def _build_request(path: str = "/servers") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "query_string": b"",
            "session": {},
            "client": ("127.0.0.1", 12345),
        }
    )


class UiServersTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_server_rejects_overlong_name_before_probe(self) -> None:
        request = _build_request()
        current_user = SimpleNamespace(id=1, role="admin")

        with (
            patch("app.routers.ui.servers.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.servers.validated_form",
                new=AsyncMock(
                    return_value={
                        "name": "x" * 121,
                        "api_url": "http://127.0.0.1",
                        "api_port": "2019",
                        "admin_api_path": "/config/",
                    }
                ),
            ),
            patch("app.routers.ui.servers.caddy_service.test_connection", new=AsyncMock()) as test_connection,
            patch("app.routers.ui.servers.server_repository.create", new=AsyncMock()) as create_server,
        ):
            response = await servers.create_server(request, session=object())

        test_connection.assert_not_awaited()
        create_server.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/servers")
        self.assertEqual(
            request.session["flashes"],
            [{
                "category": "danger",
                "message": "Server name must be between 1 and 120 characters.",
            }],
        )

    async def test_create_server_rejects_overlong_api_url_before_probe(self) -> None:
        request = _build_request()
        current_user = SimpleNamespace(id=1, role="admin")

        with (
            patch("app.routers.ui.servers.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.servers.validated_form",
                new=AsyncMock(
                    return_value={
                        "name": "edge-node",
                        "api_url": f"http://{'a' * 300}.example.test",
                        "api_port": "2019",
                        "admin_api_path": "/config/",
                    }
                ),
            ),
            patch("app.routers.ui.servers.caddy_service.test_connection", new=AsyncMock()) as test_connection,
            patch("app.routers.ui.servers.server_repository.create", new=AsyncMock()) as create_server,
        ):
            response = await servers.create_server(request, session=object())

        test_connection.assert_not_awaited()
        create_server.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/servers")
        self.assertEqual(
            request.session["flashes"],
            [{
                "category": "danger",
                "message": "API URL must be between 1 and 255 characters.",
            }],
        )

    async def test_create_server_rejects_overlong_admin_api_path_after_normalization(self) -> None:
        request = _build_request()
        current_user = SimpleNamespace(id=1, role="admin")

        with (
            patch("app.routers.ui.servers.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.servers.validated_form",
                new=AsyncMock(
                    return_value={
                        "name": "edge-node",
                        "api_url": "http://127.0.0.1",
                        "api_port": "2019",
                        "admin_api_path": "a" * 121,
                    }
                ),
            ),
            patch("app.routers.ui.servers.caddy_service.test_connection", new=AsyncMock()) as test_connection,
            patch("app.routers.ui.servers.server_repository.create", new=AsyncMock()) as create_server,
        ):
            response = await servers.create_server(request, session=object())

        test_connection.assert_not_awaited()
        create_server.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/servers")
        self.assertEqual(
            request.session["flashes"],
            [{
                "category": "danger",
                "message": "Admin API path must not exceed 120 characters.",
            }],
        )

    async def test_create_server_imports_live_config_and_redirects_to_templates_reference(self) -> None:
        request = _build_request()
        session = AsyncMock()
        session.begin_nested = MagicMock()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        config_payload = {"apps": {"http": {"servers": {}}}}
        created_server = SimpleNamespace(id=7, name="edge-1", last_pinged=None)
        config = SimpleNamespace(id=11)
        imported_definition = SimpleNamespace(
            domain="caddy.sv2.cirrio.de",
            template_name="edge-1 (caddy.sv2.cirrio.de)",
            caddyfile="reverse_proxy {{upstream}}",
            upstream="10.30.0.140:8000",
            ssl_enabled=True,
        )
        template = SimpleNamespace(id=5)
        site = SimpleNamespace(id=17)

        with (
            patch("app.routers.ui.servers.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.servers.validated_form",
                new=AsyncMock(
                    return_value={
                        "name": "edge-1",
                        "api_url": "http://10.30.0.1",
                        "api_port": "2019",
                        "admin_api_path": "/config/",
                        "active": "on",
                        "tags": "prod",
                    }
                ),
            ),
            patch("app.routers.ui.servers.caddy_service.test_connection", new=AsyncMock(return_value=config_payload)) as test_connection,
            patch("app.routers.ui.servers.caddy_service.extract_site_definitions", return_value=[imported_definition]),
            patch("app.routers.ui.servers.server_repository.create", new=AsyncMock(return_value=created_server)) as create_server,
            patch("app.routers.ui.servers.config_template_repository.get_by_name", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.servers.config_template_repository.get_by_caddyfile", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.servers.config_template_repository.create", new=AsyncMock(return_value=template)) as create_template,
            patch("app.routers.ui.servers.site_repository.get_by_domain", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.servers.site_repository.create", new=AsyncMock(return_value=site)) as create_site,
            patch("app.routers.ui.servers.config_repository.create", new=AsyncMock(return_value=config)) as create_config,
            patch("app.routers.ui.servers.server_repository.update", new=AsyncMock()) as update_server,
            patch("app.routers.ui.servers.audit_commit_and_flash", new=AsyncMock()) as audit_commit,
            patch("app.routers.ui.servers.publish_resource_event", new=AsyncMock()) as publish_resource_event,
        ):
            response = await servers.create_server(request, session=session)

        test_connection.assert_awaited_once()
        create_server.assert_awaited_once()
        create_template.assert_awaited_once()
        create_site.assert_awaited_once()
        create_config.assert_awaited_once()
        update_server.assert_awaited_once_with(session, created_server, active_config_id=11)
        audit_commit.assert_awaited_once()
        self.assertEqual(
            publish_resource_event.await_args_list,
            [
                unittest.mock.call("server", "created", "7"),
                unittest.mock.call("site", "created", "17"),
            ],
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/templates?server_id=7")

    async def test_sync_server_config_marks_active_snapshot_and_redirects_to_templates_reference(self) -> None:
        request = _build_request("/servers/7/sync")
        session = AsyncMock()
        session.begin_nested = MagicMock()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        server = SimpleNamespace(id=7, name="edge-1", active_config_id=None)
        config = SimpleNamespace(id=11)
        config_payload = {"apps": {"http": {"servers": {}}}}
        imported_definition = SimpleNamespace(
            domain="caddy.sv2.cirrio.de",
            template_name="edge-1 (caddy.sv2.cirrio.de)",
            caddyfile="reverse_proxy {{upstream}}",
            upstream="10.30.0.140:8000",
            ssl_enabled=True,
        )
        template = SimpleNamespace(id=5)
        site = SimpleNamespace(id=17)

        with (
            patch("app.routers.ui.servers.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.servers.validated_form", new=AsyncMock(return_value={})),
            patch("app.routers.ui.servers.server_repository.get_by_id", new=AsyncMock(return_value=server)),
            patch("app.routers.ui.servers.caddy_service.fetch_config", new=AsyncMock(return_value=config_payload)),
            patch("app.routers.ui.servers.caddy_service.extract_site_definitions", return_value=[imported_definition]),
            patch("app.routers.ui.servers.config_template_repository.get_by_name", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.servers.config_template_repository.get_by_caddyfile", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.servers.config_template_repository.create", new=AsyncMock(return_value=template)) as create_template,
            patch("app.routers.ui.servers.site_repository.get_by_domain", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.servers.site_repository.create", new=AsyncMock(return_value=site)) as create_site,
            patch("app.routers.ui.servers.config_repository.create", new=AsyncMock(return_value=config)) as create_config,
            patch("app.routers.ui.servers.server_repository.update", new=AsyncMock()) as update_server,
            patch("app.routers.ui.servers.audit_commit_and_flash", new=AsyncMock()) as audit_commit,
            patch("app.routers.ui.servers.publish_resource_event", new=AsyncMock()) as publish_resource_event,
        ):
            response = await servers.sync_server_config(request, server_id=7, session=session)

        create_template.assert_awaited_once()
        create_site.assert_awaited_once()
        create_config.assert_awaited_once()
        update_server.assert_awaited_once_with(session, server, active_config_id=11)
        audit_commit.assert_awaited_once()
        publish_resource_event.assert_awaited_once_with("site", "created", "17")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/templates?server_id=7")

    async def test_create_server_reuses_existing_template_with_identical_caddyfile(self) -> None:
        request = _build_request()
        request.scope["client"] = ("127.0.0.2", 12345)
        session = AsyncMock()
        session.begin_nested = MagicMock()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        config_payload = {"apps": {"http": {"servers": {}}}}
        created_server = SimpleNamespace(id=7, name="edge-1", last_pinged=None)
        config = SimpleNamespace(id=11)
        imported_definition = SimpleNamespace(
            domain="caddy.sv2.cirrio.de",
            template_name="edge-1 (caddy.sv2.cirrio.de)",
            caddyfile="reverse_proxy {{upstream}}",
            upstream="10.30.0.140:8000",
            ssl_enabled=True,
        )
        existing_template = SimpleNamespace(id=23, name="shared-template")
        site = SimpleNamespace(id=17)

        with (
            patch("app.routers.ui.servers.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.servers.validated_form",
                new=AsyncMock(
                    return_value={
                        "name": "edge-1",
                        "api_url": "http://10.30.0.1",
                        "api_port": "2019",
                        "admin_api_path": "/config/",
                        "active": "on",
                        "tags": "prod",
                    }
                ),
            ),
            patch("app.routers.ui.servers.caddy_service.test_connection", new=AsyncMock(return_value=config_payload)),
            patch("app.routers.ui.servers.caddy_service.extract_site_definitions", return_value=[imported_definition]),
            patch("app.routers.ui.servers.server_repository.create", new=AsyncMock(return_value=created_server)),
            patch("app.routers.ui.servers.config_template_repository.get_by_name", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.servers.config_template_repository.get_by_caddyfile", new=AsyncMock(return_value=existing_template)),
            patch("app.routers.ui.servers.config_template_repository.create", new=AsyncMock()) as create_template,
            patch("app.routers.ui.servers.site_repository.get_by_domain", new=AsyncMock(return_value=None)),
            patch("app.routers.ui.servers.site_repository.create", new=AsyncMock(return_value=site)) as create_site,
            patch("app.routers.ui.servers.config_repository.create", new=AsyncMock(return_value=config)),
            patch("app.routers.ui.servers.server_repository.update", new=AsyncMock()),
            patch("app.routers.ui.servers.audit_commit_and_flash", new=AsyncMock()),
            patch("app.routers.ui.servers.publish_resource_event", new=AsyncMock()),
        ):
            response = await servers.create_server(request, session=session)

        create_template.assert_not_awaited()
        create_site.assert_awaited_once_with(
            session,
            domain="caddy.sv2.cirrio.de",
            config_template_id=23,
            enabled=True,
            description="Imported from server 'edge-1'.",
            variables={"upstream": "10.30.0.140:8000"},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/templates?server_id=7")

    async def test_create_server_reports_import_conflicts_separately_from_duplicate_name(self) -> None:
        request = _build_request()
        session = AsyncMock()
        session.begin_nested = MagicMock()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        config_payload = {"apps": {"http": {"servers": {}}}}
        created_server = SimpleNamespace(id=7, name="edge-1", last_pinged=None)

        with (
            patch("app.routers.ui.servers.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.servers.validated_form",
                new=AsyncMock(
                    return_value={
                        "name": "edge-1",
                        "api_url": "http://10.30.0.1",
                        "api_port": "2019",
                        "admin_api_path": "/config/",
                    }
                ),
            ),
            patch("app.routers.ui.servers.caddy_service.test_connection", new=AsyncMock(return_value=config_payload)),
            patch("app.routers.ui.servers.server_repository.create", new=AsyncMock(return_value=created_server)),
            patch("app.routers.ui.servers.caddy_service.extract_site_definitions", return_value=[]),
            patch(
                "app.routers.ui.servers.config_repository.create",
                new=AsyncMock(side_effect=IntegrityError("stmt", "params", Exception("conflict"))),
            ),
        ):
            response = await servers.create_server(request, session=session)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/templates?server_id=7")
        self.assertEqual(
            request.session["flashes"],
            [
                {
                    "category": "warning",
                    "message": "A site or Caddyfile conflict occurred during import; server was created without sites.",
                },
                {
                    "category": "success",
                    "message": "Server 'edge-1' created and imported 0 site(s).",
                },
            ],
        )

    async def test_sync_server_config_rolls_back_then_persists_only_offline_status(self) -> None:
        request = _build_request("/servers/7/sync")
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        server = SimpleNamespace(id=7, name="edge-1", active_config_id=None, status="online", last_pinged=None)

        with (
            patch("app.routers.ui.servers.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.servers.validated_form", new=AsyncMock(return_value={})),
            patch("app.routers.ui.servers.server_repository.get_by_id", new=AsyncMock(return_value=server)),
            patch(
                "app.routers.ui.servers.caddy_service.fetch_config",
                new=AsyncMock(side_effect=servers.CaddyServiceError("down")),
            ),
            patch("app.routers.ui.servers.server_repository.update", new=AsyncMock()) as update_server,
        ):
            response = await servers.sync_server_config(request, server_id=7, session=session)

        session.rollback.assert_awaited_once()
        update_server.assert_awaited_once_with(
            session,
            server,
            status="offline",
            last_pinged=server.last_pinged,
        )
        session.commit.assert_awaited_once()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/templates?server_id=7")
        self.assertEqual(
            request.session["flashes"],
            [{
                "category": "danger",
                "message": "Could not pull the live configuration: down",
            }],
        )


if __name__ == "__main__":
    unittest.main()