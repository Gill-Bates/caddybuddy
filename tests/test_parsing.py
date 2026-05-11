#!/usr/bin/env python3
#
# tests/test_parsing.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import patch

from app.utils import parsing


class ParsingTests(unittest.TestCase):
    def test_parse_json_object_rejects_deeply_nested_json_with_value_error(self) -> None:
        with patch("app.utils.parsing.json.loads", side_effect=RecursionError("maximum recursion depth exceeded")):
            with self.assertRaisesRegex(ValueError, "must be valid JSON and must not exceed the maximum nesting depth"):
                parsing.parse_json_object('{"a": {}}', "Variables")

    def test_parse_json_object_requires_object_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "Variables must be a JSON object"):
            parsing.parse_json_object(json.dumps(["not", "an", "object"]), "Variables")

    def test_parse_expires_days_returns_none_for_invalid_or_non_positive_values(self) -> None:
        self.assertIsNone(parsing.parse_expires_days(None))
        self.assertIsNone(parsing.parse_expires_days(""))
        self.assertIsNone(parsing.parse_expires_days("abc"))
        self.assertIsNone(parsing.parse_expires_days("0"))
        self.assertIsNone(parsing.parse_expires_days("-5"))

    def test_parse_expires_days_caps_large_values(self) -> None:
        fixed_now = datetime(2026, 5, 11, 12, 0, tzinfo=parsing.UTC)

        with patch("app.utils.parsing.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fixed_now
            mocked_datetime.side_effect = datetime

            expires_at = parsing.parse_expires_days(str(parsing._MAX_EXPIRY_DAYS + 50))

        self.assertEqual((expires_at - fixed_now).days, parsing._MAX_EXPIRY_DAYS)


if __name__ == "__main__":
    unittest.main()