#!/usr/bin/env python3
#
# tests/test_ui_ssllabs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import os
import re
import unittest
from datetime import UTC, datetime, timedelta
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
from app.routers.ui.ssllabs import router as ssllabs_router
from app.dependencies.web import redirect_to
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
        self.onboarding_patcher = patch(
            "app.routers.ui._common.get_onboarding_state",
            new=AsyncMock(return_value=SimpleNamespace(status="completed")),
        )
        self.onboarding_patcher.start()

    def tearDown(self) -> None:
        self.onboarding_patcher.stop()
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

    @staticmethod
    def _extract_csrf_token(html: str) -> str:
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if match is None:
            raise AssertionError("csrf_token input not found in SSL Labs page")
        return match.group(1)

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
            schedule_frequency="weekly",
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
        self.assertIn("On (weekly)", response.text)
        self.assertNotIn(">Monthly<", response.text)
        self.assertIn('>Report</a>', response.text)
        self.assertIn('>Report</button>', response.text)
        self.assertIn('>Scan</button>', response.text)
        self.assertLess(response.text.index('>Report</a>'), response.text.index('>Scan</button>'))
        self.assertIn('aria-label="No SSL Labs report available yet for www.example.com"', response.text)
        self.assertRegex(
            response.text,
            r'<button[^>]*aria-label="No SSL Labs report available yet for www\.example\.com"[^>]*disabled[^>]*>Report</button>',
        )
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
        self.assertIn('<div class="ssllabs-domain-card"', response.text)
        self.assertNotIn('<details class="ssllabs-domain-card"', response.text)
        self.assertIn('class="ssllabs-domain-card__summary"', response.text)
        self.assertNotIn('class="ssllabs-domain-card__actions"', response.text)
        self.assertIn('class="ssllabs-domain-card__quick-actions"', response.text)
        self.assertRegex(
            response.text,
            r'<div class="ssllabs-domain-card__summary">[\s\S]*class="ssllabs-domain-card__inline-scheduler"[\s\S]*class="ssllabs-domain-card__quick-actions"',
        )
        self.assertNotIn('class="cell-actions ssllabs-actions"', response.text)
        self.assertIn("data-require-csrf", response.text)
        self.assertIn("data-loading-submit-button", response.text)
        self.assertNotIn(">Save</button>", response.text)
        self.assertIn("data-ssllabs-autosave", response.text)
        self.assertIn('class="badge bg-success ssllabs-result__grade ssllabs-result__grade--compact"', response.text)
        self.assertIn('class="status-pill status-pill--online ssllabs-result__status-badge ssllabs-result__status-badge--compact"', response.text)
        self.assertIn('data-ui-lint-ignore-click-target', response.text)
        self.assertIn('data-ui-lint-dynamic', response.text)
        self.assertIn('class="ssllabs-result__endpoints"', response.text)
        self.assertIn("IPv4", response.text)
        self.assertIn("IPv6", response.text)
        self.assertIn("Not scanned yet", response.text)

    def test_ssllabs_mobile_scheduler_form_stretches_full_width(self) -> None:
        css_path = Path(__file__).resolve().parents[1] / "app/static/css/app.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn(
            ".ssllabs-domain-card__inline-scheduler {\n        align-items: stretch;\n        width: 100%;",
            css,
        )
        self.assertNotIn(
            ".ssllabs-domain-card__actions {\n    display: grid;\n    grid-template-columns: minmax(0, 1fr);",
            css,
        )
        self.assertIn(
            ".ssllabs-domain-card .ssllabs-schedule-form {\n        display: flex;\n        width: 100%;\n        min-width: 0;",
            css,
        )
        self.assertIn(
            ".ssllabs-domain-card .ssllabs-schedule-form .form-select {\n        width: 100%;\n        min-width: 0;\n        flex: 1 1 auto;",
            css,
        )
        self.assertNotIn(
            ".ssllabs-domain-card .ssllabs-schedule-form {\n        display: grid;\n        grid-template-columns: minmax(0, 1fr) auto;",
            css,
        )

    def test_ssllabs_page_marks_mixed_filter_grade_for_mixed_endpoints(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        target = SimpleNamespace(
            id=1,
            host="mixed.example.com",
            schedule_frequency=None,
            next_scheduled_at=None,
        )
        site = SimpleNamespace(id=1, site_name="Mixed", domain="mixed.example.com", enabled=True)
        scan = SimpleNamespace(
            grade=None,
            status="ready",
            completed_at=datetime.now(UTC),
            started_at=datetime.now(UTC) - timedelta(hours=1),
            endpoint_count=2,
            error_message=None,
            result_json={
                "endpoints": [
                    {"ipAddress": "203.0.113.10", "grade": "A+", "statusMessage": "Ready"},
                    {"ipAddress": "2001:db8::10", "grade": "B", "statusMessage": "Ready"},
                ]
            },
        )

        with (
            patch("app.routers.ui.ssllabs.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.ssllabs.ssllabs_service.sync_targets", new=AsyncMock()),
            patch(
                "app.routers.ui.ssllabs.ssllabs_repository.list_targets_with_latest_scans",
                new=AsyncMock(return_value=[(target, site, scan)]),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/ssl-labs")

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-ssllabs-grade="mixed"', response.text)

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
        self.assertIn('>Report</button>', response.text)
        self.assertNotIn('>Report</a>', response.text)
        self.assertIn("public hostname", response.text)
        self.assertIn('aria-label="No SSL Labs report available yet for service.internal"', response.text)
        self.assertRegex(
            response.text,
            r'<button[^>]*aria-label="No SSL Labs report available yet for service\.internal"[^>]*disabled[^>]*>Report</button>',
        )
        self.assertRegex(response.text, r'name="schedule_frequency"[^>]*disabled')
        self.assertRegex(response.text, r'name="mode"[^>]*value="fresh"[^>]*disabled')
        self.assertNotIn("Open last report", response.text)

    def test_schedule_enable_triggers_auto_queue_for_stale_scan_without_crashing(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        target = SimpleNamespace(
            id=1,
            host="example.com",
            schedule_frequency=None,
            next_scheduled_at=None,
        )
        site = SimpleNamespace(id=1, site_name="Marketing", domain="example.com", enabled=True)
        latest_scan = SimpleNamespace(
            completed_at=datetime.now(UTC) - timedelta(days=10),
            started_at=datetime.now(UTC) - timedelta(days=10, hours=1),
            next_poll_at=None,
            status="ready",
        )

        with (
            patch("app.routers.ui.ssllabs.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.ssllabs.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.ssllabs.ssllabs_service.update_schedule", new=AsyncMock()),
            patch(
                "app.routers.ui.ssllabs.ssllabs_repository.get_latest_scan_for_target",
                new=AsyncMock(return_value=latest_scan),
            ),
            patch(
                "app.routers.ui.ssllabs.ssllabs_service.request_scan",
                new=AsyncMock(return_value=SimpleNamespace(created=True, host="example.com")),
            ) as request_scan,
            patch(
                "app.routers.ui.ssllabs.ssllabs_repository.list_targets_with_latest_scans",
                new=AsyncMock(return_value=[(target, site, latest_scan)]),
            ),
        ):
            with TestClient(app) as client:
                page = client.get("/ssl-labs")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/ssl-labs/1/schedule",
                    data={"csrf_token": csrf_token, "schedule_frequency": "on"},
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/ssl-labs")
        request_scan.assert_awaited_once_with(target_id=1, force_new=False)

    def test_schedule_enable_shows_warning_when_auto_queue_fails(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        target = SimpleNamespace(id=1, host="example.com", schedule_frequency=None, next_scheduled_at=None)
        site = SimpleNamespace(id=1, site_name="Marketing", domain="example.com", enabled=True)
        latest_scan = SimpleNamespace(
            completed_at=datetime.now(UTC) - timedelta(days=10),
            started_at=datetime.now(UTC) - timedelta(days=10, hours=1),
            next_poll_at=None,
            status="ready",
        )

        with (
            patch("app.routers.ui.ssllabs.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.ssllabs.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.ssllabs.ssllabs_service.update_schedule", new=AsyncMock()),
            patch(
                "app.routers.ui.ssllabs.ssllabs_repository.get_latest_scan_for_target",
                new=AsyncMock(return_value=latest_scan),
            ),
            patch(
                "app.routers.ui.ssllabs.ssllabs_service.request_scan",
                new=AsyncMock(side_effect=ValueError("queue failed")),
            ),
            patch("app.routers.ui.ssllabs.logger.warning") as log_warning,
            patch(
                "app.routers.ui.ssllabs.ssllabs_repository.list_targets_with_latest_scans",
                new=AsyncMock(return_value=[(target, site, latest_scan)]),
            ),
        ):
            with TestClient(app) as client:
                page = client.get("/ssl-labs")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/ssl-labs/1/schedule",
                    data={"csrf_token": csrf_token, "schedule_frequency": "on"},
                    follow_redirects=True,
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Schedule was enabled, but the initial scan could not be queued.", response.text)
        log_warning.assert_called_once()

    def test_scan_rejects_invalid_mode(self) -> None:
        app = self._build_app()
        current_user = SimpleNamespace(username="admin", role="admin")
        target = SimpleNamespace(id=1, host="example.com", schedule_frequency=None, next_scheduled_at=None)
        site = SimpleNamespace(id=1, site_name="Marketing", domain="example.com", enabled=True)

        with (
            patch("app.routers.ui.ssllabs.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.ssllabs.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.ssllabs.ssllabs_repository.list_targets_with_latest_scans",
                new=AsyncMock(return_value=[(target, site, None)]),
            ),
            patch("app.routers.ui.ssllabs.ssllabs_service.request_scan", new=AsyncMock()) as request_scan,
        ):
            with TestClient(app) as client:
                page = client.get("/ssl-labs")
                csrf_token = self._extract_csrf_token(page.text)
                response = client.post(
                    "/ssl-labs/1/scan",
                    data={"csrf_token": csrf_token, "mode": "frseh"},
                    follow_redirects=True,
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Invalid SSL Labs scan mode.", response.text)
        request_scan.assert_not_awaited()

    def test_mutating_routes_redirect_to_onboarding_when_wizard_not_completed(self) -> None:
        current_user = SimpleNamespace(username="admin", role="admin")
        request = SimpleNamespace()
        session = AsyncMock()

        with (
            patch("app.routers.ui.ssllabs.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.ssllabs.require_onboarding_completed", new=AsyncMock(return_value=redirect_to("/onboarding"))),
            patch("app.routers.ui.ssllabs.validated_form", new=AsyncMock(return_value={"csrf_token": "token", "mode": "fresh"})),
            patch("app.routers.ui.ssllabs.ssllabs_service.request_scan", new=AsyncMock()) as request_scan,
        ):
            from app.routers.ui.ssllabs import start_ssllabs_scan

            handler = getattr(start_ssllabs_scan, "__wrapped__", start_ssllabs_scan)
            page = asyncio.run(handler(request=request, target_id=1, session=session))

        self.assertEqual(page.status_code, 303)
        self.assertEqual(page.headers["location"], "/onboarding")
        request_scan.assert_not_awaited()

    def test_filter_grade_prefers_failed_status_over_stale_grade(self) -> None:
        from app.routers.ui.ssllabs import _normalize_filter_grade

        latest_scan = SimpleNamespace(grade="A+", status="failed", error_message="boom")

        self.assertEqual(_normalize_filter_grade(latest_scan, []), "failed")


class ParseScheduleFrequencyTests(unittest.TestCase):
    def test_on_values_map_to_weekly(self) -> None:
        from app.utils.ssllabs import parse_ssllabs_schedule_control

        for value in ("on", "weekly", "true", "1", "yes", "ON", " Weekly "):
            self.assertEqual(parse_ssllabs_schedule_control(value), "weekly")

    def test_off_values_map_to_none(self) -> None:
        from app.utils.ssllabs import parse_ssllabs_schedule_control

        for value in ("", "  ", "off", "false", "0", "no"):
            self.assertIsNone(parse_ssllabs_schedule_control(value))

    def test_unknown_value_raises(self) -> None:
        from app.utils.ssllabs import parse_ssllabs_schedule_control

        with self.assertRaises(ValueError):
            parse_ssllabs_schedule_control("monthly")
