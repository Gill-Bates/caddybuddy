#!/usr/bin/env python3
#
# tests/test_ui_common.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config.settings import get_settings
from tests.env_overrides import ModuleEnv

_ENV = ModuleEnv()

from app.routers.ui._common import require_onboarding_completed


def tearDownModule() -> None:
    _ENV.restore()


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

    async def test_sidebar_brand_places_the_kicker_below_the_logo(self) -> None:
        template = Path("app/templates/base.html").read_text(encoding="utf-8")

        logo_index = template.index('class="brand-mark__logo-wrap"')
        kicker_index = template.index('class="brand-mark__kicker"')
        self.assertLess(logo_index, kicker_index)

    async def test_sidebar_backdrop_drops_its_blur_for_reduced_transparency(self) -> None:
        css = Path("app/static/css/app.css").read_text(encoding="utf-8")

        blur_index = css.index("    .sidebar-backdrop {\n        backdrop-filter: blur(4px);")
        override_index = css.index(
            "@media (prefers-reduced-transparency: reduce) {\n    .sidebar-backdrop {\n        backdrop-filter: none;"
        )
        # Same specificity: an override placed before the blur rule loses to it.
        self.assertLess(blur_index, override_index)


if __name__ == "__main__":
    unittest.main()
