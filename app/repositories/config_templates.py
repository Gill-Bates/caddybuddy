#!/usr/bin/env python3
#
# app/repositories/config_templates.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import ConfigRevision, ConfigTemplate


_UNSET: Final = object()
_REVISION_VERSION_RETRY_LIMIT = 3


class ConfigTemplateRepository:
    """Repository for ConfigTemplate CRUD operations."""

    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count(ConfigTemplate.id)))
        return int(result.scalar_one())

    async def list_all(
        self,
        session: AsyncSession,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ConfigTemplate]:
        statement = (
            select(ConfigTemplate)
            .options(selectinload(ConfigTemplate.sites))
            .order_by(ConfigTemplate.name.asc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        if offset is not None:
            statement = statement.offset(offset)
        result = await session.execute(statement)
        return list(result.scalars().unique().all())

    async def get_by_id(self, session: AsyncSession, template_id: int) -> ConfigTemplate | None:
        result = await session.execute(
            select(ConfigTemplate)
            .options(selectinload(ConfigTemplate.sites))
            .where(ConfigTemplate.id == template_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, session: AsyncSession, name: str) -> ConfigTemplate | None:
        result = await session.execute(
            select(ConfigTemplate)
            .options(selectinload(ConfigTemplate.sites))
            .where(ConfigTemplate.name == name)
        )
        return result.scalar_one_or_none()

    async def get_by_checksum(self, session: AsyncSession, checksum: str) -> ConfigTemplate | None:
        result = await session.execute(
            select(ConfigTemplate).where(ConfigTemplate.checksum == checksum)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        *,
        name: str,
        caddyfile: str,
        description: str | None = None,
        variables: dict[str, Any] | None = None,
        created_by: str | None = None,
    ) -> ConfigTemplate:
        checksum = await asyncio.to_thread(ConfigTemplate.compute_checksum, caddyfile)
        template = ConfigTemplate(
            name=name,
            description=description,
            caddyfile=caddyfile,
            checksum=checksum,
            variables=variables or {},
        )
        session.add(template)
        await session.flush()

        # Create initial revision
        revision = ConfigRevision(
            template_id=template.id,
            version=1,
            caddyfile=caddyfile,
            checksum=checksum,
            variables=variables or {},
            change_summary="Initial creation",
            created_by=created_by,
        )
        session.add(revision)
        await session.flush()

        return template

    async def update(
        self,
        session: AsyncSession,
        template: ConfigTemplate,
        *,
        name: str | object = _UNSET,
        caddyfile: str | object = _UNSET,
        description: str | None | object = _UNSET,
        variables: dict[str, Any] | object = _UNSET,
        change_summary: str | None | object = _UNSET,
        updated_by: str | None = None,
    ) -> ConfigTemplate:
        await self._lock_template_for_update(session, template.id)

        caddyfile_changed = caddyfile is not _UNSET and caddyfile != template.caddyfile

        if name is not _UNSET:
            template.name = name
        if description is not _UNSET:
            template.description = description
        if caddyfile is not _UNSET:
            template.caddyfile = caddyfile
            template.checksum = await asyncio.to_thread(ConfigTemplate.compute_checksum, caddyfile)
        if variables is not _UNSET:
            template.variables = variables

        await session.flush()

        if caddyfile_changed:
            await self._create_revision_with_retry(
                session,
                template=template,
                change_summary=(
                    change_summary if change_summary is not _UNSET else "Configuration updated"
                ),
                updated_by=updated_by,
            )

        return template

    async def delete(self, session: AsyncSession, template: ConfigTemplate) -> None:
        await session.delete(template)
        await session.flush()

    async def _get_max_version(self, session: AsyncSession, template_id: int) -> int:
        result = await session.execute(
            select(func.max(ConfigRevision.version)).where(ConfigRevision.template_id == template_id)
        )
        return result.scalar_one() or 0

    async def get_revisions(
        self,
        session: AsyncSession,
        template_id: int,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ConfigRevision]:
        statement = (
            select(ConfigRevision)
            .where(ConfigRevision.template_id == template_id)
            .order_by(ConfigRevision.version.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        if offset is not None:
            statement = statement.offset(offset)
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def get_revision(
        self, session: AsyncSession, template_id: int, version: int
    ) -> ConfigRevision | None:
        result = await session.execute(
            select(ConfigRevision).where(
                ConfigRevision.template_id == template_id,
                ConfigRevision.version == version,
            )
        )
        return result.scalar_one_or_none()

    async def _lock_template_for_update(self, session: AsyncSession, template_id: int) -> None:
        await session.execute(
            select(ConfigTemplate.id)
            .where(ConfigTemplate.id == template_id)
            .with_for_update()
        )

    async def _create_revision_with_retry(
        self,
        session: AsyncSession,
        *,
        template: ConfigTemplate,
        change_summary: str | None,
        updated_by: str | None,
    ) -> None:
        for attempt in range(_REVISION_VERSION_RETRY_LIMIT):
            try:
                async with session.begin_nested():
                    max_version = await self._get_max_version(session, template.id)
                    revision = ConfigRevision(
                        template_id=template.id,
                        version=max_version + 1,
                        caddyfile=template.caddyfile,
                        checksum=template.checksum,
                        variables=template.variables,
                        change_summary=change_summary,
                        created_by=updated_by,
                    )
                    session.add(revision)
                    await session.flush()
                return
            except IntegrityError as exc:
                if not self._is_revision_version_conflict(exc) or attempt == _REVISION_VERSION_RETRY_LIMIT - 1:
                    raise

    @staticmethod
    def _is_revision_version_conflict(exc: IntegrityError) -> bool:
        message = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
        return (
            "uq_config_revisions_template_version" in message
            or "config_revisions.template_id, config_revisions.version" in message
        )


config_template_repository = ConfigTemplateRepository()
