#!/usr/bin/env python3
#
# tests/test_config_template_repository.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.entities import ConfigRevision, ConfigTemplate
from app.repositories.config_templates import (
    ConcurrentTemplateUpdateError,
    TemplateAlreadyExistsError,
    _MAX_CADDYFILE_SIZE,
    ConfigTemplateRepository,
)


class ConfigTemplateRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.repository = ConfigTemplateRepository()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_create_snapshots_variables_for_template_and_initial_revision(self) -> None:
        payload = {"upstream": "127.0.0.1:8080", "headers": {"X-Test": "1"}}

        async with self.session_factory() as session:
            template = await self.repository.create(
                session,
                name="base",
                caddyfile="example.com { reverse_proxy {$upstream} }",
                variables=payload,
                created_by="alice",
            )
            payload["headers"]["X-Test"] = "mutated"
            await session.commit()

        async with self.session_factory() as session:
            stored_template = await self.repository.get_by_id(session, template.id)
            revisions = await self.repository.get_revisions(session, template.id)

        self.assertIsNotNone(stored_template)
        self.assertEqual(stored_template.variables["headers"]["X-Test"], "1")
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0].variables["headers"]["X-Test"], "1")

    async def test_update_creates_revision_for_variable_only_change(self) -> None:
        async with self.session_factory() as session:
            template = await self.repository.create(
                session,
                name="base",
                caddyfile="example.com { reverse_proxy {$upstream} }",
                variables={"upstream": "127.0.0.1:8080"},
            )
            await session.commit()

        async with self.session_factory() as session:
            template = await self.repository.get_by_id(session, template.id)
            assert template is not None
            await self.repository.update(
                session,
                template,
                variables={"upstream": "127.0.0.1:9090"},
                updated_by="bob",
            )
            await session.commit()

        async with self.session_factory() as session:
            revisions = await self.repository.get_revisions(session, template.id, limit=10)

        self.assertEqual([revision.version for revision in revisions], [2, 1])
        self.assertEqual(revisions[0].variables["upstream"], "127.0.0.1:9090")
        self.assertEqual(revisions[0].created_by, "bob")

    async def test_list_all_rejects_invalid_limit(self) -> None:
        async with self.session_factory() as session:
            with self.assertRaisesRegex(ValueError, "limit must be between 1 and 500"):
                await self.repository.list_all(session, limit=0)

    async def test_create_rejects_oversized_caddyfile(self) -> None:
        oversized = "a" * (_MAX_CADDYFILE_SIZE + 1)

        async with self.session_factory() as session:
            with self.assertRaisesRegex(ValueError, "Caddyfile exceeds maximum size"):
                await self.repository.create(
                    session,
                    name="too-large",
                    caddyfile=oversized,
                )

    async def test_update_raises_domain_error_on_stale_write(self) -> None:
        async with self.session_factory() as session:
            template = await self.repository.create(
                session,
                name="base",
                caddyfile="example.com { respond \"ok\" }",
            )
            await session.commit()

        async with self.session_factory() as session_a, self.session_factory() as session_b:
            template_a = await self.repository.get_by_id(session_a, template.id)
            template_b = await self.repository.get_by_id(session_b, template.id)
            assert template_a is not None
            assert template_b is not None

            await self.repository.update(
                session_a,
                template_a,
                description="first",
            )
            await session_a.commit()

            with self.assertRaises(ConcurrentTemplateUpdateError):
                await self.repository.update(
                    session_b,
                    template_b,
                    description="second",
                )

            await session_b.rollback()

        async with self.session_factory() as session:
            persisted = await self.repository.get_by_id(session, template.id)
            revisions = await session.execute(
                select(ConfigRevision).where(ConfigRevision.template_id == template.id)
            )

        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.description, "first")
        self.assertEqual(len(list(revisions.scalars().all())), 1)

    async def test_create_rejects_duplicate_checksum_with_domain_error(self) -> None:
        async with self.session_factory() as session:
            await self.repository.create(
                session,
                name="base",
                caddyfile="example.com { respond \"ok\" }",
            )
            await session.commit()

        async with self.session_factory() as session:
            with self.assertRaises(TemplateAlreadyExistsError):
                await self.repository.create(
                    session,
                    name="base-copy",
                    caddyfile="example.com { respond \"ok\" }",
                )
            await session.rollback()
