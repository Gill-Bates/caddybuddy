#!/usr/bin/env python3
#
# tests/test_logging_config.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import logging
import unittest

from app.config.logging import SuppressVerboseDependencyFilter, build_log_config


class LoggingConfigTests(unittest.TestCase):
    def test_build_log_config_suppresses_httpcore_debug_logs(self) -> None:
        config = build_log_config("DEBUG")

        self.assertEqual(config["loggers"]["httpcore"]["level"], "WARNING")
        self.assertFalse(config["loggers"]["httpcore"]["propagate"])

    def test_build_log_config_suppresses_python_multipart_debug_logs(self) -> None:
        config = build_log_config("DEBUG")

        self.assertEqual(config["loggers"]["python_multipart"]["level"], "WARNING")
        self.assertFalse(config["loggers"]["python_multipart"]["propagate"])

    def test_verbose_dependency_filter_blocks_httpcore_debug_records(self) -> None:
        record = logging.LogRecord(
            name="httpcore.http11",
            level=logging.DEBUG,
            pathname=__file__,
            lineno=1,
            msg="receive_response_headers.started request=<Request [b'GET']>",
            args=(),
            exc_info=None,
        )

        self.assertFalse(SuppressVerboseDependencyFilter().filter(record))

    def test_verbose_dependency_filter_blocks_python_multipart_debug_records(self) -> None:
        record = logging.LogRecord(
            name="python_multipart.multipart",
            level=logging.DEBUG,
            pathname=__file__,
            lineno=1,
            msg="Calling on_field_start with no data",
            args=(),
            exc_info=None,
        )

        self.assertFalse(SuppressVerboseDependencyFilter().filter(record))


if __name__ == "__main__":
    unittest.main()