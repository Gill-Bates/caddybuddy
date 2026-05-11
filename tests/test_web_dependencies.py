#!/usr/bin/env python3
#
# tests/test_web_dependencies.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from pathlib import Path
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Request

from app.dependencies import web


class WebTemplateFilterTests(unittest.TestCase):
    def test_format_datetime_accepts_naive_datetime(self) -> None:
        value = datetime(2026, 5, 8, 10, 30)
        expected = value.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M")

        formatted = web._format_datetime(value)

        self.assertEqual(formatted, expected)

    def test_days_until_returns_calendar_day_difference(self) -> None:
        fixed_now = datetime(2026, 5, 8, 10, 0, tzinfo=UTC)
        expires_at = fixed_now + timedelta(days=5, hours=4)

        with patch("app.dependencies.web.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fixed_now
            mocked_datetime.side_effect = datetime

            remaining_days = web._days_until(expires_at)

        self.assertEqual(remaining_days, 5)

    def test_expiry_badge_class_marks_near_expiry_as_warning(self) -> None:
        fixed_now = datetime(2026, 5, 8, 10, 0, tzinfo=UTC)
        expires_at = fixed_now + timedelta(days=2)

        with patch("app.dependencies.web.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fixed_now
            mocked_datetime.side_effect = datetime

            badge_class = web._expiry_badge_class(expires_at)

        self.assertEqual(badge_class, "text-bg-warning")

    def test_expiry_badge_class_marks_expired_as_danger(self) -> None:
        fixed_now = datetime(2026, 5, 8, 10, 0, tzinfo=UTC)
        expires_at = fixed_now - timedelta(days=1)

        with patch("app.dependencies.web.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fixed_now
            mocked_datetime.side_effect = datetime

            badge_class = web._expiry_badge_class(expires_at)

        self.assertEqual(badge_class, "text-bg-danger")


class WebSessionTests(unittest.TestCase):
    def test_initialize_user_session_stores_only_fingerprint_not_password_hash(self) -> None:
        request = SimpleNamespace(session={})

        with patch("app.dependencies.web.time.time", return_value=789.0):
            web.initialize_user_session(request, user_id=42, password_hash="bcrypt-hash")

        self.assertEqual(request.session["user_id"], 42)
        self.assertEqual(request.session["session_created_at"], 789.0)
        self.assertEqual(request.session["session_last_activity"], 789.0)
        self.assertEqual(request.session["user_fingerprint"], web._user_session_fingerprint("bcrypt-hash"))
        self.assertNotIn("password_hash", request.session)

    def test_refresh_session_timestamps_only_updates_last_activity(self) -> None:
        request = SimpleNamespace(session={
            "session_created_at": 123.0,
            "session_last_activity": 456.0,
        })

        with patch("app.dependencies.web.time.time", return_value=789.0):
            web.refresh_session_timestamps(request)

        self.assertEqual(request.session["session_created_at"], 123.0)
        self.assertEqual(request.session["session_last_activity"], 789.0)


class AssetIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        web._asset_integrity_by_path.cache_clear()
        web._asset_integrity_cached.cache_clear()

    def test_asset_integrity_uses_process_cache_without_reload(self) -> None:
        original_reload = web.settings.reload
        web.settings.reload = False
        self.addCleanup(setattr, web.settings, "reload", original_reload)

        with patch.object(Path, "read_bytes", return_value=b"body") as read_bytes:
            first = web.asset_integrity("css/app.css")
            second = web.asset_integrity("css/app.css")

        self.assertEqual(first, second)
        self.assertEqual(read_bytes.call_count, 1)

    def test_asset_integrity_tracks_file_changes_in_reload_mode(self) -> None:
        original_reload = web.settings.reload
        web.settings.reload = True
        self.addCleanup(setattr, web.settings, "reload", original_reload)

        stat_versions = [
            SimpleNamespace(st_mtime_ns=1, st_size=10),
            SimpleNamespace(st_mtime_ns=2, st_size=11),
        ]
        file_versions = [b"first", b"second"]

        with (
            patch.object(Path, "stat", side_effect=stat_versions),
            patch.object(Path, "read_bytes", side_effect=file_versions),
        ):
            first = web.asset_integrity("css/app.css")
            second = web.asset_integrity("css/app.css")

        self.assertNotEqual(first, second)


class WebTemplateRenderingTests(unittest.TestCase):
    def test_login_template_uses_compact_public_shell_container(self) -> None:
        class _AppStub:
            def url_path_for(self, name: str, **path_params: str) -> str:
                if name == "login_action":
                    return "/login"
                if name != "static":
                    raise AssertionError(f"Unexpected route lookup: {name}")
                return f"/static/{path_params['path']}"

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/login",
                "headers": [],
                "query_string": b"",
                "session": {},
                "client": ("127.0.0.1", 12345),
                "app": _AppStub(),
            }
        )

        response = web.render_template(request, "login.html", current_user=None)
        html = response.body.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="app-body app-body--public"', html)
        self.assertIn('class="app-container"', html)
