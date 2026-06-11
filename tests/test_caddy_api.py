#!/usr/bin/env python3
#
# tests/test_caddy_api.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import UTC, datetime
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

import app.routers.caddy_api as caddy_api
from app.config.limiter import limiter
from app.database.session import get_db_session
from app.services.caddyfile_manager import CaddyOnboardingResult, CaddySyncResult


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value


def _build_app(session: object) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(caddy_api.router)

    async def _get_session_override():
        yield session

    async def _require_admin_override() -> SimpleNamespace:
        return SimpleNamespace(is_admin=True)

    app.dependency_overrides[get_db_session] = _get_session_override
    app.dependency_overrides[caddy_api._require_admin_api_user] = _require_admin_override
    return app


class CaddyApiTests(unittest.TestCase):
    def test_caddy_status_endpoint_returns_current_runtime_status(self) -> None:
        session = SimpleNamespace(commit=AsyncMock())
        app = _build_app(session)

        with (
            TestClient(app) as client,
            patch.object(
                caddy_api,
                "get_caddy_runtime_status",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        managed=True,
                        onboarding_required=False,
                        caddyfile_path="/data/caddy/Caddyfile",
                        caddyfile_marker_present=True,
                        admin_api_reachable=True,
                        last_synced_config_sha256="abc123",
                        error=None,
                    )
                ),
            ),
        ):
            response = client.get("/api/caddy/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "managed": True,
                "onboarding_required": False,
                "caddyfile_path": "/data/caddy/Caddyfile",
                "caddyfile_marker_present": True,
                "admin_api_reachable": True,
                "last_synced_config_sha256": "abc123",
                "error": None,
            },
        )

    def test_caddy_onboard_endpoint_maps_admin_api_failure_to_503(self) -> None:
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        app = _build_app(session)

        with (
            TestClient(app) as client,
            patch.object(
                caddy_api,
                "onboard_caddy",
                new=AsyncMock(
                    return_value=CaddyOnboardingResult(
                        status="error",
                        synced=False,
                        error="Caddy Admin API unavailable.",
                        error_code="caddy_admin_unavailable",
                    )
                ),
            ),
        ):
            response = client.post("/api/caddy/onboard")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "error")
        self.assertEqual(response.json()["detail"], "Caddy Admin API unavailable.")

    def test_create_site_endpoint_returns_sync_metadata(self) -> None:
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), flush=AsyncMock())
        app = _build_app(session)
        now = datetime.now(UTC)
        site = SimpleNamespace(
            id=1,
            site_name="Example Site",
            domain="example.com",
            upstream_url="http://backend.internal:8080",
            caddy_directives="reverse_proxy backend.internal:8080",
            enabled=True,
            created_at=now,
            updated_at=now,
        )

        with (
            TestClient(app) as client,
            patch.object(caddy_api.site_repository, "create", new=AsyncMock(return_value=site)),
            patch.object(
                caddy_api,
                "sync_caddy_configuration",
                new=AsyncMock(
                    return_value=CaddySyncResult(
                        status="synced",
                        config_sha256="deadbeef",
                        synced=True,
                    )
                ),
            ),
        ):
            response = client.post(
                "/api/sites",
                json={
                    "site_name": "Example Site",
                    "domain": "Example.com",
                    "caddy_directives": "reverse_proxy backend.internal:8080",
                    "enabled": True,
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "created")
        self.assertEqual(response.json()["sync_status"], "synced")
        self.assertTrue(response.json()["synced"])
        self.assertEqual(response.json()["config_sha256"], "deadbeef")
        session.flush.assert_awaited_once()
        session.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()