#!/usr/bin/env python3
#
# tests/test_site_repository.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.exc import IntegrityError

from app.repositories.sites import DuplicateSiteError, SiteRepository


class SiteRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_all_rejects_invalid_limit(self) -> None:
        repository = SiteRepository()
        session = SimpleNamespace()

        with self.assertRaisesRegex(ValueError, "Limit must be between 1 and 500"):
            await repository.list_all(session, limit=0)

    async def test_get_by_domain_normalizes_lookup_value(self) -> None:
        repository = SiteRepository()
        expected_statements = [
            "SELECT caddy_sites.id, caddy_sites.domain, caddy_sites.upstream_url, caddy_sites.caddy_directives, caddy_sites.enabled, caddy_sites.created_at, caddy_sites.updated_at \nFROM caddy_sites \nWHERE caddy_sites.domain = 'example.com'",
            "SELECT caddy_sites.id, caddy_sites.domain, caddy_sites.upstream_url, caddy_sites.caddy_directives, caddy_sites.enabled, caddy_sites.created_at, caddy_sites.updated_at \nFROM caddy_sites ORDER BY caddy_sites.domain ASC",
        ]

        async def execute(statement):
            self.assertEqual(
                str(statement.compile(compile_kwargs={"literal_binds": True})),
                expected_statements.pop(0),
            )
            if expected_statements:
                return SimpleNamespace(scalar_one_or_none=lambda: None)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

        session = SimpleNamespace(execute=execute)

        result = await repository.get_by_domain(session, " Example.COM. ")

        self.assertIsNone(result)

    async def test_domain_exists_uses_normalized_exists_lookup(self) -> None:
        repository = SiteRepository()
        session = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 1))
        )

        exists = await repository.domain_exists(session, " Example.COM. ")

        self.assertTrue(exists)

    async def test_domain_exists_detects_overlap_in_multi_domain_rows(self) -> None:
        repository = SiteRepository()
        session = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    SimpleNamespace(scalar_one_or_none=lambda: None),
                    SimpleNamespace(all=lambda: [SimpleNamespace(id=1, domain="example.com, www.example.com")]),
                ]
            )
        )

        exists = await repository.domain_exists(session, "www.example.com")

        self.assertTrue(exists)

    async def test_create_raises_duplicate_site_error_on_unique_collision(self) -> None:
        repository = SiteRepository()
        session = SimpleNamespace(
            add=lambda site: None,
            flush=AsyncMock(side_effect=IntegrityError("stmt", "params", Exception("boom"))),
        )

        with self.assertRaisesRegex(DuplicateSiteError, "Site domain already exists"):
            await repository.create(
                session,
                domain="Example.COM.",
                caddy_directives="reverse_proxy backend.example.test:443",
            )

    async def test_update_normalizes_values_before_flush(self) -> None:
        repository = SiteRepository()
        session = SimpleNamespace(flush=AsyncMock())
        site = SimpleNamespace(
            domain="old.example.com",
            upstream_url="https://old.example.com",
            caddy_directives="reverse_proxy old.example.com",
            enabled=True,
        )

        updated = await repository.update(
            session,
            site,
            domain=" Example.COM. ",
            caddy_directives="reverse_proxy backend.example.test:8443",
            enabled=False,
        )

        self.assertIs(updated, site)
        self.assertEqual(site.domain, "example.com")
        self.assertEqual(site.caddy_directives, "reverse_proxy backend.example.test:8443")
        self.assertEqual(site.upstream_url, "http://backend.example.test:8443")
        self.assertFalse(site.enabled)
        session.flush.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()