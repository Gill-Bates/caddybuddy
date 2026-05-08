#!/usr/bin/env python3
#
# tests/test_entities.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest

from app.models.entities import ConfigTemplate


class ConfigTemplateRelationshipTests(unittest.TestCase):
    def test_revisions_are_deleted_with_template(self) -> None:
        relationship = ConfigTemplate.revisions.property

        self.assertIn("delete", relationship.cascade)
        self.assertIn("delete-orphan", relationship.cascade)
        self.assertTrue(relationship.passive_deletes)


if __name__ == "__main__":
    unittest.main()