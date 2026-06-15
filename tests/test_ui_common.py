#!/usr/bin/env python3
#
# tests/test_ui_common.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
import unittest
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

from app.routers.ui._common import require_onboarding_completed


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


class UICommonTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    async def test_require_onboarding_completed_redirects_for_incomplete_states(self) -> None:
        for status in ("not_started", "in_progress", "failed"):
            with self.subTest(status=status):
                with patch(
                    "app.routers.ui._common.get_onboarding_state",
                    new=AsyncMock(return_value=SimpleNamespace(status=status)),
                ):
                    response = await require_onboarding_completed(AsyncMock())

                self.assertIsNotNone(response)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["location"], "/onboarding")

    async def test_require_onboarding_completed_ignores_runtime_onboarding_required(self) -> None:
        with patch(
            "app.routers.ui._common.get_onboarding_state",
            new=AsyncMock(return_value=SimpleNamespace(status="completed")),
        ):
            response = await require_onboarding_completed(AsyncMock())

        self.assertIsNone(response)

    async def test_require_onboarding_completed_allows_completed_runtime(self) -> None:
        with patch(
            "app.routers.ui._common.get_onboarding_state",
            new=AsyncMock(return_value=SimpleNamespace(status="completed")),
        ):
            response = await require_onboarding_completed(AsyncMock())

        self.assertIsNone(response)


if __name__ == "__main__":
    unittest.main()
