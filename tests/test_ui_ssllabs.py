#!/usr/bin/env python3
#
# tests/test_ui_ssllabs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
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
from app.routers.ui.ssllabs import router as ssllabs_router
from tests.ui_test_app import build_ui_test_app


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

    def _build_app(self):
        return build_ui_test_app(
            ssllabs_router,
            session_override=self._session_override,
            stub_routes=[
                ("GET", "/", "home_page"),
                ("GET", "/caddyfile", "caddyfile_page"),
                ("GET", "/sites", "sites_page"),
                ("POST", "/logout", "logout_action"),
            ],
        )

    def test_ssllabs_page_renders_rows_and_external_notice(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        target = SimpleNamespace(
            id=1,
            host="example.com",
            schedule_frequency="weekly",
            next_scheduled_at=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        )
        second_target = SimpleNamespace(
            id=2,
            host="www.example.com",
            schedule_frequency="monthly",
            next_scheduled_at=datetime(2026, 6, 27, 12, 0, tzinfo=UTC),
        )
        site = SimpleNamespace(id=1, site_name="Marketing", domain="example.com, www.example.com", enabled=True)
        scan = SimpleNamespace(
            grade="A+",
            status="ready",
            completed_at=datetime.now(UTC) - timedelta(hours=1),
            started_at=datetime.now(UTC) - timedelta(hours=2),
            endpoint_count=2,
            error_message=None,
            result_json={
                "endpoints": [
                    {"ipAddress": "203.0.113.10", "grade": "A+", "statusMessage": "Ready"},
                    {"ipAddress": "2001:db8::10", "grade": "A", "statusMessage": "Ready"},
                ]
            },
        )

        with (
            patch("app.routers.ui.ssllabs.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.ssllabs.ssllabs_service.sync_targets", new=AsyncMock()),
            patch(
                "app.routers.ui.ssllabs.ssllabs_repository.list_targets_with_latest_scans",
                new=AsyncMock(return_value=[(target, site, scan), (second_target, site, None)]),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/ssl-labs")

        self.assertEqual(response.status_code, 200)
        self.assertIn("SSL Labs", response.text)
        self.assertIn("Marketing", response.text)
        self.assertIn("example.com", response.text)
        self.assertIn("www.example.com", response.text)
        self.assertIn('data-ssllabs-filter-root', response.text)
        self.assertIn('data-ssllabs-search', response.text)
        self.assertIn('data-ssllabs-grade-filter', response.text)
        self.assertIn('data-ssllabs-visible-count', response.text)
        self.assertIn('data-ssllabs-visible-label', response.text)
        self.assertIn('aria-live="polite"', response.text)
        self.assertIn("Domains found", response.text)
        self.assertIn("Scheduler", response.text)
        self.assertIn('class="app-page ssllabs-page"', response.text)
        self.assertIn('class="ssllabs-scrollbox"', response.text)
        self.assertIn('data-ssllabs-site-row', response.text)
        self.assertIn('data-ssllabs-filter-card', response.text)
        self.assertIn('data-ssllabs-grade="a+"', response.text)
        self.assertIn('/static/js/ssllabs-filter.js', response.text)
        self.assertIn('No domains match the current filter.', response.text)
        self.assertIn('class="site-row-title"', response.text)
        self.assertIn('class="status-dot site-status-dot status-dot--online"', response.text)
        self.assertNotIn('class="ssllabs-site-domains mt-2"', response.text)
        self.assertNotIn('class="badge bg-secondary bg-opacity-25 text-body-secondary ssllabs-site-domain-badge">example.com</span>', response.text)
        self.assertNotIn('class="badge bg-secondary bg-opacity-25 text-body-secondary ssllabs-site-domain-badge">www.example.com</span>', response.text)
        self.assertIn("A+", response.text)
        self.assertIn("Weekly", response.text)
        self.assertIn("Every 30 days", response.text)
        self.assertIn('>Report</a>', response.text)
        self.assertIn('>Scan</button>', response.text)
        self.assertNotIn("View report", response.text)
        self.assertNotIn("Check SSL Labs", response.text)
        self.assertIn("Start SSL Labs checks here", response.text)
        self.assertIn('class="table align-middle mb-0 ssllabs-table"', response.text)
        self.assertIn('class="ssllabs-table__site-col"', response.text)
        self.assertIn('class="ssllabs-table__domains-col"', response.text)
        self.assertIn('class="table-responsive ssllabs-table-wrap"', response.text)
        self.assertIn('data-label="Site"', response.text)
        self.assertIn('data-label="Domains"', response.text)
        self.assertIn('class="ssllabs-domain-list"', response.text)
        self.assertIn('class="ssllabs-domain-card"', response.text)
        self.assertIn("data-auto-submit-form", response.text)
        self.assertIn("data-auto-submit-field", response.text)
        self.assertNotIn(">Save</button>", response.text)
        self.assertIn('class="badge bg-success ssllabs-result__grade"', response.text)
        self.assertIn('class="ssllabs-result__headline"', response.text)
        self.assertIn('class="ssllabs-result__endpoints"', response.text)
        self.assertIn("IPv4", response.text)
        self.assertIn("IPv6", response.text)
        self.assertIn("2 endpoints", response.text)

    def test_ssllabs_page_disables_actions_for_non_public_host(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        target = SimpleNamespace(
            id=1,
            host="service.internal",
            schedule_frequency=None,
            next_scheduled_at=None,
        )
        site = SimpleNamespace(id=1, site_name="Internal", domain="service.internal", enabled=True)

        with (
            patch("app.routers.ui.ssllabs.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.ssllabs.ssllabs_service.sync_targets", new=AsyncMock()),
            patch(
                "app.routers.ui.ssllabs.ssllabs_repository.list_targets_with_latest_scans",
                new=AsyncMock(return_value=[(target, site, None)]),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/ssl-labs")

        self.assertEqual(response.status_code, 200)
        self.assertIn("public hostname", response.text)
        self.assertRegex(response.text, r'name="schedule_frequency"[^>]*disabled')
        self.assertRegex(response.text, r'name="mode"[^>]*value="fresh"[^>]*disabled')
        self.assertNotIn("Open last report", response.text)