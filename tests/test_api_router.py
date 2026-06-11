#!/usr/bin/env python3
#
# tests/test_api_router.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

_ENV_OVERRIDES = {
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CADDYBUDDY_ADMIN_PASSWORD": "unit-test-password",
}
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}

for key, value in _ENV_OVERRIDES.items():
    os.environ[key] = value

import app.routers.api as system_api
from app.database.session import get_db_session


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value


def _build_app(session: object) -> FastAPI:
    app = FastAPI()
    app.include_router(system_api.router)

    async def _get_session_override():
        yield session

    app.dependency_overrides[get_db_session] = _get_session_override
    return app


class ApiRouterTests(unittest.TestCase):
    def test_events_endpoint_requires_authenticated_user(self) -> None:
        app = _build_app(SimpleNamespace())

        with (
            TestClient(app) as client,
            patch.object(system_api, "get_session_user", new=AsyncMock(return_value=None)),
        ):
            response = client.get("/api/v1/events")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Authentication required."})

    def test_events_endpoint_returns_sse_headers_for_authenticated_user(self) -> None:
        app = _build_app(SimpleNamespace())

        async def _empty_events():
            if False:
                yield

        with (
            TestClient(app) as client,
            patch.object(system_api, "get_session_user", new=AsyncMock(return_value=SimpleNamespace(id=1))),
            patch.object(system_api.event_bus, "subscribe", return_value=_empty_events()),
        ):
            response = client.get("/api/v1/events")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-cache, no-transform")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertNotIn("connection", response.headers)

    def test_ready_endpoint_returns_503_when_admin_api_unavailable(self) -> None:
        app = _build_app(SimpleNamespace())

        with (
            TestClient(app) as client,
            patch.object(
                system_api,
                "get_caddy_runtime_status",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        error=None,
                        onboarding_required=False,
                        admin_api_reachable=False,
                    )
                ),
            ),
            patch.object(system_api, "get_build_info", return_value={"version": "1.2.3", "commit": "abc"}),
        ):
            response = client.get("/api/v1/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Caddy Admin API unavailable."})

    def test_ready_endpoint_returns_ok_when_runtime_is_ready(self) -> None:
        app = _build_app(SimpleNamespace())

        with (
            TestClient(app) as client,
            patch.object(
                system_api,
                "get_caddy_runtime_status",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        error=None,
                        onboarding_required=False,
                        admin_api_reachable=True,
                    )
                ),
            ),
            patch.object(system_api, "get_build_info", return_value={"version": "1.2.3", "commit": "abc"}),
        ):
            response = client.get("/api/v1/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "app": "CaddyBuddy", "version": "1.2.3"},
        )

    def test_dashboard_metrics_endpoint_returns_metrics_for_authenticated_user(self) -> None:
        app = _build_app(SimpleNamespace())

        with (
            TestClient(app) as client,
            patch.object(system_api, "get_session_user", new=AsyncMock(return_value=SimpleNamespace(id=1))),
            patch.object(
                system_api,
                "get_dashboard_metrics",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        domain_count=12,
                        enabled_domain_count=10,
                        valid_certificate_count=5,
                        expired_certificate_count=2,
                        expiring_soon_certificate_count=1,
                        caddy_service_status="Running",
                        caddy_service_uptime="Unavailable",
                        caddy_version="v2.10.0",
                    )
                ),
            ),
        ):
            response = client.get("/api/v1/dashboard/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "domain_count": 12,
                "enabled_domain_count": 10,
                "valid_certificate_count": 5,
                "expired_certificate_count": 2,
                "expiring_soon_certificate_count": 1,
                "caddy_service_status": "Running",
                "caddy_service_uptime": "Unavailable",
                "caddy_version": "v2.10.0",
            },
        )

    def test_ssllabs_registration_status_uses_database_email(self) -> None:
        app = _build_app(SimpleNamespace())
        check_status = AsyncMock(return_value=True)

        with (
            TestClient(app) as client,
            patch.object(system_api, "get_session_user", new=AsyncMock(return_value=SimpleNamespace(id=1, is_admin=True))),
            patch.object(system_api, "get_settings", return_value=SimpleNamespace(ssllabs_api_base_url="https://api.ssllabs.com/api/v4")),
            patch.object(system_api, "get_ssllabs_email", new=AsyncMock(return_value="team@example.com")),
            patch.object(system_api, "check_email_registration_status", new=check_status),
        ):
            response = client.get("/api/v1/ssllabs/registration-status")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("email", response.json())
        self.assertIn("masked_email", response.json())
        self.assertTrue(response.json()["is_registered"])
        check_status.assert_awaited_once_with(
            "team@example.com",
            api_base_url="https://api.ssllabs.com/api/v4",
        )

    def test_ssllabs_registration_status_reports_missing_database_email(self) -> None:
        app = _build_app(SimpleNamespace())

        with (
            TestClient(app) as client,
            patch.object(system_api, "get_session_user", new=AsyncMock(return_value=SimpleNamespace(id=1, is_admin=True))),
            patch.object(system_api, "get_settings", return_value=SimpleNamespace(ssllabs_api_base_url="https://api.ssllabs.com/api/v4")),
            patch.object(system_api, "get_ssllabs_email", new=AsyncMock(return_value=None)),
        ):
            response = client.get("/api/v1/ssllabs/registration-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "No SSL Labs email configured.")

    def test_ssllabs_registration_status_hides_internal_exception_details(self) -> None:
        app = _build_app(SimpleNamespace())

        with (
            TestClient(app) as client,
            patch.object(system_api, "get_session_user", new=AsyncMock(return_value=SimpleNamespace(id=1, is_admin=True))),
            patch.object(system_api, "get_settings", return_value=SimpleNamespace(ssllabs_api_base_url="https://api.ssllabs.com/api/v4")),
            patch.object(system_api, "get_ssllabs_email", new=AsyncMock(return_value="team@example.com")),
            patch.object(
                system_api,
                "check_email_registration_status",
                new=AsyncMock(side_effect=RuntimeError("internal dns failure")),
            ),
        ):
            response = client.get("/api/v1/ssllabs/registration-status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["is_registered"], None)
        self.assertEqual(response.json()["message"], "Could not check SSL Labs registration status.")
        self.assertNotIn("internal dns failure", response.text)


class EventStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_format_sse_data_prefixes_each_line(self) -> None:
        payload = system_api._format_sse_data('{"x":1}\n{"y":2}')

        self.assertEqual(payload, 'data: {"x":1}\ndata: {"y":2}\n\n')

    async def test_event_stream_emits_heartbeat_and_closes_iterator(self) -> None:
        class _ClosableIterator:
            def __init__(self) -> None:
                self.closed = False

            def __aiter__(self) -> "_ClosableIterator":
                return self

            async def __anext__(self):
                await asyncio.sleep(3600)
                raise StopAsyncIteration

            async def aclose(self) -> None:
                self.closed = True

        events = _ClosableIterator()
        stream = system_api._event_stream(events)

        first_chunk = await anext(stream)
        self.assertEqual(first_chunk, ": keep-alive\n\n")

        await stream.aclose()
        self.assertTrue(events.closed)


if __name__ == "__main__":
    unittest.main()