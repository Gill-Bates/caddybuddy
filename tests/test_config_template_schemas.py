#!/usr/bin/env python3
#
# tests/test_config_template_schemas.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.schemas.config_templates import (
    ConfigRevisionRead,
    ConfigTemplateBase,
    ConfigTemplateList,
    ConfigTemplateRead,
    ConfigTemplateUpdate,
)


class ConfigTemplateSchemaTests(unittest.TestCase):
    def test_base_schema_rejects_oversized_description(self) -> None:
        with self.assertRaises(ValidationError):
            ConfigTemplateBase(
                name="edge",
                description="x" * 2001,
                caddyfile="respond ok",
                variables={},
            )

    def test_base_schema_rejects_too_many_variables(self) -> None:
        with self.assertRaises(ValidationError) as exc:
            ConfigTemplateBase(
                name="edge",
                caddyfile="respond ok",
                variables={f"key{i}": "value" for i in range(51)},
            )

        self.assertIn("must not contain more than 50 entries", str(exc.exception))

    def test_update_schema_rejects_oversized_variable_key_or_value(self) -> None:
        with self.assertRaises(ValidationError):
            ConfigTemplateUpdate(variables={"k" * 201: "value"})

        with self.assertRaises(ValidationError):
            ConfigTemplateUpdate(variables={"upstream": "x" * 10001})

    def test_read_schema_requires_timezone_aware_datetimes(self) -> None:
        with self.assertRaises(ValidationError):
            ConfigTemplateRead(
                id=1,
                name="edge",
                description=None,
                caddyfile="respond ok",
                variables={},
                checksum="abc",
                created_at=datetime(2026, 5, 11, 12, 0),
                updated_at=datetime(2026, 5, 11, 12, 0),
            )

        model = ConfigTemplateRead(
            id=1,
            name="edge",
            description=None,
            caddyfile="respond ok",
            variables={},
            checksum="abc",
            created_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
            updated_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        )

        self.assertEqual(model.created_at.tzinfo, UTC)

    def test_list_schema_requires_timezone_aware_datetimes(self) -> None:
        with self.assertRaises(ValidationError):
            ConfigTemplateList(
                id=1,
                name="edge",
                description=None,
                checksum="abc",
                created_at=datetime(2026, 5, 11, 12, 0),
                updated_at=datetime(2026, 5, 11, 12, 0),
            )

    def test_revision_schema_validates_variables_and_timezone(self) -> None:
        with self.assertRaises(ValidationError):
            ConfigRevisionRead(
                id=1,
                template_id=1,
                version=1,
                caddyfile="respond ok",
                checksum="abc",
                variables={f"key{i}": "value" for i in range(51)},
                change_summary=None,
                created_by="alice",
                created_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
            )

        with self.assertRaises(ValidationError):
            ConfigRevisionRead(
                id=1,
                template_id=1,
                version=1,
                caddyfile="respond ok",
                checksum="abc",
                variables={"upstream": "127.0.0.1:8080"},
                change_summary=None,
                created_by="alice",
                created_at=datetime(2026, 5, 11, 12, 0),
            )


if __name__ == "__main__":
    unittest.main()