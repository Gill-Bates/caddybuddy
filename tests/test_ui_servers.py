#!/usr/bin/env python3

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

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


if __name__ == "__main__":
    unittest.main()