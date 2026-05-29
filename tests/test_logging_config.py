#!/usr/bin/env python3
#
# tests/test_logging_config.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import logging
import re
import unittest

from app.config.logging import (
    SuppressVerboseDependencyFilter,
    TimestampAccessFormatter,
    TimestampDefaultFormatter,
    _redact_sensitive_query_params,
    build_log_config,
)


class LoggingConfigTests(unittest.TestCase):
    def test_redact_sensitive_query_params_handles_delimiters_and_common_secret_names(self) -> None:
        value = (
            "GET /callback?foo=bar&token=abc&client_secret=def;refresh_token=ghi "
            "jwt=jkl api-key=mno authorization_code=oauth otp=123456 nested_token=keep"
        )

        redacted = _redact_sensitive_query_params(value)

        self.assertIn("token=***REDACTED***", redacted)
        self.assertIn("client_secret=***REDACTED***", redacted)
        self.assertIn("refresh_token=***REDACTED***", redacted)
        self.assertIn("jwt=***REDACTED***", redacted)
        self.assertIn("api-key=***REDACTED***", redacted)
        self.assertIn("authorization_code=***REDACTED***", redacted)
        self.assertIn("otp=***REDACTED***", redacted)
        self.assertIn("nested_token=keep", redacted)

    def test_default_formatter_redacts_message_and_color_message_and_uses_utc_timestamp(self) -> None:
        formatter = TimestampDefaultFormatter(fmt="%(timestamp)s %(message)s %(color_message)s", use_colors=False)
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="Request /login?password=hunter2&sid=abc123",
            args=(),
            exc_info=None,
        )
        record.created = 1_700_000_000.123
        record.message = record.getMessage()
        record.__dict__["color_message"] = "access_token=secret-value"

        output = formatter.formatMessage(record)

        self.assertIn("password=***REDACTED***", output)
        self.assertIn("sid=***REDACTED***", output)
        self.assertIn("access_token=***REDACTED***", output)
        self.assertNotIn("hunter2", output)
        self.assertNotIn("secret-value", output)
        self.assertRegex(output, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z ")

    def test_access_formatter_redacts_request_line_from_args(self) -> None:
        formatter = TimestampAccessFormatter(
            fmt='%(timestamp)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            use_colors=False,
        )
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='%s - "%s %s HTTP/%s" %s',
            args=("127.0.0.1:1234", "GET", "/login?api_key=secret", "1.1", 200),
            exc_info=None,
        )
        record.created = 1_700_000_000.123

        output = formatter.format(record)

        self.assertIn("api_key=***REDACTED***", output)
        self.assertNotIn("api_key=secret", output)

    def test_build_log_config_keeps_noisy_dependency_loggers_at_requested_level(self) -> None:
        config = build_log_config("debug")

        self.assertEqual(config["loggers"]["httpx"]["level"], "DEBUG")
        self.assertEqual(config["loggers"]["sqlalchemy.engine"]["level"], "WARNING")
        self.assertEqual(config["loggers"]["sqlalchemy.pool"]["level"], "WARNING")
        self.assertEqual(config["handlers"]["default"]["filters"], ["suppress_verbose_dependencies"])

    def test_suppress_verbose_dependency_filter_matches_config_intent(self) -> None:
        cases = [
            ("httpx", logging.DEBUG, False),
            ("httpx", logging.INFO, True),
            ("app.service", logging.DEBUG, True),
        ]

        for logger_name, level, expected in cases:
            with self.subTest(logger_name=logger_name, level=level):
                record = logging.LogRecord(
                    name=logger_name,
                    level=level,
                    pathname=__file__,
                    lineno=1,
                    msg="test",
                    args=(),
                    exc_info=None,
                )

                self.assertIs(SuppressVerboseDependencyFilter().filter(record), expected)


if __name__ == "__main__":
    unittest.main()