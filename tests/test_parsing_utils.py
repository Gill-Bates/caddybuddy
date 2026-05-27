#!/usr/bin/env python3
#
# tests/test_parsing_utils.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import UTC, datetime
import unittest

from app.utils.parsing import parse_expires_days, parse_json_object, pretty_json, split_csv


class ParsingUtilsTests(unittest.TestCase):
    def test_split_csv_trims_and_filters_empty_items(self) -> None:
        self.assertEqual(split_csv(" one, , two ,three "), ["one", "two", "three"])

    def test_split_csv_rejects_too_many_items(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain more than 2 items"):
            split_csv("a,b,c", max_items=2)

    def test_split_csv_rejects_overlong_item(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds 3 characters"):
            split_csv("abcd", max_item_length=3)

    def test_parse_json_object_requires_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload must be a JSON object"):
            parse_json_object("[]", "payload")

    def test_parse_json_object_rejects_excessive_depth(self) -> None:
        deeply_nested = '{"a":' * 33 + '1' + '}' * 33
        with self.assertRaisesRegex(ValueError, "maximum nesting depth of 32"):
            parse_json_object(deeply_nested, "payload")

    def test_parse_json_object_rejects_invalid_json(self) -> None:
        with self.assertRaisesRegex(ValueError, "payload must be valid JSON"):
            parse_json_object("{", "payload")

    def test_pretty_json_wraps_non_json_compatible_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "JSON-compatible"):
            pretty_json({"expires_at": datetime.now(UTC)})

    def test_parse_expires_days_returns_none_for_blank_input(self) -> None:
        self.assertIsNone(parse_expires_days("  "))
        self.assertIsNone(parse_expires_days(None))

    def test_parse_expires_days_rejects_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer"):
            parse_expires_days("abc")
        with self.assertRaisesRegex(ValueError, "positive integer"):
            parse_expires_days("1.5")

    def test_parse_expires_days_rejects_zero_and_excessive_days(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            parse_expires_days("0")
        with self.assertRaisesRegex(ValueError, "must not exceed 30 days"):
            parse_expires_days("31", max_days=30)

    def test_parse_expires_days_returns_future_timestamp(self) -> None:
        expires_at = parse_expires_days("2", max_days=30)

        self.assertIsNotNone(expires_at)
        assert expires_at is not None
        delta = expires_at - datetime.now(UTC)
        self.assertGreater(delta.total_seconds(), 24 * 60 * 60)


if __name__ == "__main__":
    unittest.main()