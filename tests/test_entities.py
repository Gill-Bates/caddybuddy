#!/usr/bin/env python3
#
# tests/test_entities.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest

from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex

from app.models.entities import CaddyServer, ConfigTemplate, Deployment, Site


class ConfigTemplateRelationshipTests(unittest.TestCase):
    def test_revisions_are_deleted_with_template(self) -> None:
        relationship = ConfigTemplate.revisions.property

        self.assertIn("delete", relationship.cascade)
        self.assertIn("delete-orphan", relationship.cascade)
        self.assertTrue(relationship.passive_deletes)


class EntityValidationTests(unittest.TestCase):
    def test_caddy_server_rejects_out_of_range_api_port(self) -> None:
        with self.assertRaisesRegex(ValueError, "api_port must be an integer between 1 and 65535"):
            CaddyServer(
                name="edge-1",
                api_url="https://admin.example.com",
                api_port=0,
                admin_api_path="/config/",
            )

        with self.assertRaisesRegex(ValueError, "api_port must be an integer between 1 and 65535"):
            CaddyServer(
                name="edge-2",
                api_url="https://admin.example.com",
                api_port=65536,
                admin_api_path="/config/",
            )

    def test_site_normalizes_domain_names_consistently(self) -> None:
        site = Site(domain=" Example.COM ", config_template_id=1)

        self.assertEqual(site.domain, "example.com")

    def test_active_deployment_partial_index_uses_enum_value(self) -> None:
        index = next(
            idx for idx in Deployment.__table__.indexes if idx.name == "uq_active_deployment_per_site_server"
        )

        compiled = str(CreateIndex(index).compile(dialect=sqlite.dialect()))

        self.assertIn("status = 'deployed'", compiled)
        self.assertNotIn("status = 'DEPLOYED'", compiled)

    def test_rollback_source_relationship_uses_explicit_foreign_key(self) -> None:
        relationship = Deployment.rollback_source.property

        self.assertEqual(
            relationship._user_defined_foreign_keys,
            {Deployment.__table__.c.rollback_deployment_id},
        )


if __name__ == "__main__":
    unittest.main()