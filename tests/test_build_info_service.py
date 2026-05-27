#!/usr/bin/env python3
#
# tests/test_build_info_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.services.build_info as build_info_module


class BuildInfoServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        build_info_module.get_build_info.cache_clear()

    def test_get_build_info_parses_build_info_key_value_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            (base_dir / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            (base_dir / "BUILD_INFO").write_text(
                "APP_VERSION=1.0.0\n"
                "GIT_SHA=4ed7d8dd0304b320206248a3da0d5aa04f2367ea\n"
                "BUILD_DATE=2026-05-27T12:42:20Z\n",
                encoding="utf-8",
            )

            with (
                patch.object(build_info_module, "get_settings", return_value=SimpleNamespace(base_dir=base_dir)),
                patch.dict(build_info_module.os.environ, {}, clear=True),
            ):
                build_info = build_info_module.get_build_info()

        self.assertEqual(build_info["version"], "1.0.0")
        self.assertEqual(build_info["commit"], "4ed7d8dd0304b320206248a3da0d5aa04f2367ea")
        self.assertEqual(build_info["build_date"], "2026-05-27T12:42:20Z")


if __name__ == "__main__":
    unittest.main()