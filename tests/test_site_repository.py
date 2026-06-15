#!/usr/bin/env python3
#
# tests/test_site_repository.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.repositories.sites import DuplicateSiteError, SiteRepository


class SiteRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_all_rejects_invalid_limit(self) -> None:
        repository = SiteRepository()
        session = SimpleNamespace()

        with self.assertRaisesRegex(ValueError, "Limit must be between 1 and 500"):
            await repository.list_all(session, limit=0)

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
            get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None, all=lambda: [])),
            add=lambda site: None,
            flush=AsyncMock(
                side_effect=IntegrityError(
                    "INSERT INTO caddy_sites (domain) VALUES (?)",
                    {"domain": "example.com"},
                    Exception("UNIQUE constraint failed: caddy_sites.domain"),
                )
            ),
        )

        with self.assertRaisesRegex(DuplicateSiteError, "Site domain already exists"):
            await repository.create(
                session,
                site_name="Example Site",
                domain="Example.COM.",
                caddy_directives="reverse_proxy backend.example.test:443",
            )

    async def test_create_defaults_upstream_for_enabled_non_proxy_site(self) -> None:
        repository = SiteRepository()
        session = SimpleNamespace(
            get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None, all=lambda: [])),
            add=lambda site: None,
            flush=AsyncMock(),
        )

        site = await repository.create(
            session,
            site_name="Example Site",
            domain="Example.COM.",
            caddy_directives='respond "ok" 200',
            enabled=True,
        )

        self.assertEqual(site.upstream_url, "http://example.com")
        self.assertTrue(site.enabled)

    async def test_create_acquires_domain_lock_before_lookup(self) -> None:
        repository = SiteRepository()
        session = SimpleNamespace(
            get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None, all=lambda: [])),
            add=lambda site: None,
            flush=AsyncMock(),
        )

        await repository.create(
            session,
            site_name="Example Site",
            domain="Example.COM.",
            caddy_directives="reverse_proxy backend.example.test:443",
        )

        first_statement = session.execute.await_args_list[0].args[0]
        first_statement_text = str(first_statement.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("caddybuddy_state", first_statement_text)
        self.assertIn("site_domain_lock", first_statement_text)

    async def test_update_normalizes_values_before_flush(self) -> None:
        repository = SiteRepository()
        session = SimpleNamespace(
            get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None, all=lambda: [])),
            flush=AsyncMock(),
        )
        site = SimpleNamespace(
            site_name="Old Site",
            domain="old.example.com",
            upstream_url="https://old.example.com",
            caddy_directives="reverse_proxy old.example.com",
            enabled=True,
        )

        updated = await repository.update(
            session,
            site,
            site_name="Example Site",
            domain=" Example.COM. ",
            caddy_directives="reverse_proxy backend.example.test:8443",
            enabled=False,
        )

        self.assertIs(updated, site)
        self.assertEqual(site.site_name, "Example Site")
        self.assertEqual(site.domain, "example.com")
        self.assertEqual(site.caddy_directives, "reverse_proxy backend.example.test:8443")
        self.assertEqual(site.upstream_url, "http://backend.example.test:8443")
        self.assertFalse(site.enabled)
        session.flush.assert_awaited_once()

    async def test_update_does_not_mutate_site_before_validation_completes(self) -> None:
        repository = SiteRepository()
        session = SimpleNamespace(flush=AsyncMock())
        site = SimpleNamespace(
            site_name="Old Site",
            domain="old.example.com",
            upstream_url="https://old.example.com",
            caddy_directives="reverse_proxy old.example.com",
            enabled=True,
        )

        with self.assertRaisesRegex(ValueError, "caddy_directives cannot be empty"):
            await repository.update(
                session,
                site,
                site_name="New Site",
                caddy_directives="   ",
            )

        self.assertEqual(site.site_name, "Old Site")
        self.assertEqual(site.caddy_directives, "reverse_proxy old.example.com")
        self.assertEqual(site.upstream_url, "https://old.example.com")
        session.flush.assert_not_awaited()


class SiteRepositoryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self._temp_dir.name) / "sites.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._temp_dir.cleanup()

    async def test_get_by_domain_finds_normalized_domain(self) -> None:
        repository = SiteRepository()

        async with self.session_factory() as session:
            await repository.create(
                session,
                site_name="Example",
                domain="Example.COM.",
                caddy_directives="reverse_proxy backend.example.test:443",
            )
            await session.commit()

        async with self.session_factory() as session:
            site = await repository.get_by_domain(session, " example.com ")

        self.assertIsNotNone(site)
        self.assertEqual(site.domain, "example.com")

    async def test_domain_exists_ignores_overlap_for_excluded_row(self) -> None:
        repository = SiteRepository()

        async with self.session_factory() as session:
            site = await repository.create(
                session,
                site_name="Example",
                domain="example.com, www.example.com",
                caddy_directives="reverse_proxy backend.example.test:443",
            )
            await session.commit()

        async with self.session_factory() as session:
            exists = await repository.domain_exists(
                session,
                "www.example.com",
                exclude_id=site.id,
            )

        self.assertFalse(exists)


if __name__ == "__main__":
    unittest.main()
