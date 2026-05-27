#!/usr/bin/env python3
#
# tests/test_ui_ssllabs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
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

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.database.session import get_db_session
from app.middleware.csrf import CSRFMiddleware, SecurityHeadersMiddleware
from app.routers.ui.ssllabs import router as ssllabs_router


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


class UISslLabsTests(unittest.TestCase):
    def setUp(self) -> None:
        for key, value in _ENV_OVERRIDES.items():
            os.environ[key] = value
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()

    @staticmethod
    async def _session_override():
        yield AsyncMock()

    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(CSRFMiddleware)
        app.add_middleware(SessionMiddleware, secret_key="unit-test-secret-key")
        app.add_middleware(SecurityHeadersMiddleware)
        app.mount("/static", StaticFiles(directory="/opt/caddybuddy/app/static"), name="static")

        @app.get("/", name="home_page")
        async def _home_page() -> dict[str, str]:
            return {"status": "ok"}

        @app.get("/caddyfile", name="caddyfile_page")
        async def _caddyfile_page() -> dict[str, str]:
            return {"status": "ok"}

        @app.get("/sites", name="sites_page")
        async def _sites_page() -> dict[str, str]:
            return {"status": "ok"}

        @app.post("/logout", name="logout_action")
        async def _logout_action() -> dict[str, str]:
            return {"status": "ok"}

        app.include_router(ssllabs_router)
        app.dependency_overrides[get_db_session] = self._session_override
        return app

    def test_ssllabs_page_renders_rows_and_external_notice(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        target = SimpleNamespace(
            id=1,
            host="example.com",
            schedule_frequency="weekly",
            next_scheduled_at=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        )
        site = SimpleNamespace(id=1, domain="example.com, www.example.com")
        scan = SimpleNamespace(
            grade="A+",
            status="ready",
            completed_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            started_at=datetime(2026, 5, 27, 11, 59, tzinfo=UTC),
            endpoint_count=2,
            error_message=None,
        )

        with (
            patch("app.routers.ui.ssllabs.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.ssllabs.ssllabs_service.sync_targets", new=AsyncMock()),
            patch(
                "app.routers.ui.ssllabs.ssllabs_repository.list_targets_with_latest_scans",
                new=AsyncMock(return_value=[(target, site, scan)]),
            ),
            patch("app.routers.ui.ssllabs.ssllabs_service.masked_email", return_value="s***@example.com"),
        ):
            with TestClient(app) as client:
                response = client.get("/ssl-labs")

        self.assertEqual(response.status_code, 200)
        self.assertIn("SSL Labs", response.text)
        self.assertIn("example.com", response.text)
        self.assertIn("A+", response.text)
        self.assertIn("Weekly", response.text)
        self.assertIn("Check SSL Labs", response.text)
        self.assertIn("external Qualys servers", response.text)

    def test_ssllabs_page_disables_actions_for_non_public_host(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        target = SimpleNamespace(
            id=1,
            host="service.internal",
            schedule_frequency=None,
            next_scheduled_at=None,
        )
        site = SimpleNamespace(id=1, domain="service.internal")

        with (
            patch("app.routers.ui.ssllabs.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.ssllabs.ssllabs_service.sync_targets", new=AsyncMock()),
            patch(
                "app.routers.ui.ssllabs.ssllabs_repository.list_targets_with_latest_scans",
                new=AsyncMock(return_value=[(target, site, None)]),
            ),
            patch("app.routers.ui.ssllabs.ssllabs_service.masked_email", return_value="s***@example.com"),
        ):
            with TestClient(app) as client:
                response = client.get("/ssl-labs")

        self.assertEqual(response.status_code, 200)
        self.assertIn("public hostname", response.text)
        self.assertIn("disabled", response.text)