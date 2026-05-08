from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.dependencies import web


class WebTemplateFilterTests(unittest.TestCase):
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