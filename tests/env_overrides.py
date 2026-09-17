#!/usr/bin/env python3
#
# tests/env_overrides.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
from collections.abc import Mapping
from types import MappingProxyType

from app.config.settings import get_settings

TEST_ENV: Mapping[str, str] = MappingProxyType({
    "CB_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CB_ADMIN_PASSWORD": "UnitTestPassword-123A",
    "CADDYBUDDY_ADMIN_PASSWORD": "UnitTestPassword-123A",
})


class ModuleEnv:
    """Sets env overrides at construction and restores the prior values on ``restore()``.

    Construct at module level before importing app modules that read settings,
    and call ``restore()`` from ``tearDownModule``.
    """

    def __init__(self, overrides: Mapping[str, str] = TEST_ENV) -> None:
        self.overrides = dict(overrides)
        self._original = {key: os.environ.get(key) for key in self.overrides}
        self.apply()

    def apply(self) -> None:
        os.environ.update(self.overrides)
        get_settings.cache_clear()

    def restore(self) -> None:
        for key, original_value in self._original.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value
        get_settings.cache_clear()
