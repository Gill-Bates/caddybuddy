#!/usr/bin/env python3
#
# app/repositories/config_templates.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import copy
import json
import logging
from time import perf_counter
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.config.settings import get_settings
from app.models.entities import ConfigRevision, ConfigTemplate


_DEFAULT_LIST_LIMIT = 100
_MAX_LIST_LIMIT = 500
_MAX_CADDYFILE_SIZE = 2 * 1024 * 1024

logger = logging.getLogger(__name__)


class _UnsetType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


_UNSET: Final = _UnsetType()


class ConcurrentTemplateUpdateError(RuntimeError):
    """Raised when a config template update loses an optimistic concurrency race."""


class TemplateAlreadyExistsError(RuntimeError):
    """Raised when a template with the same checksum already exists."""


def _revision_retry_limit() -> int:
    return get_settings().config_template_revision_retry_limit


def _checksum_timeout_seconds() -> float:
    return get_settings().config_template_checksum_timeout_seconds


def _normalize_limit(limit: int | None) -> int:
    if limit is None:
        return _DEFAULT_LIST_LIMIT
    if limit < 1 or limit > _MAX_LIST_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_LIST_LIMIT}")
    return limit


def _normalize_offset(offset: int | None) -> int:
    if offset is None:
        return 0
    if offset < 0:
        raise ValueError("offset must be non-negative")
    return offset


def _snapshot_variables(variables: dict[str, Any] | None) -> dict[str, Any]:
    if variables is None:
        return {}
    if not isinstance(variables, dict):
        raise TypeError("variables must be a dictionary")
    snapshot = copy.deepcopy(variables)
    try:
        json.dumps(snapshot)
    except TypeError as exc:
        raise ValueError("Config template variables must be JSON-serializable") from exc
    return snapshot


async def _compute_checksum(caddyfile: str) -> str:
    encoded_size = len(caddyfile.encode("utf-8"))
    if encoded_size > _MAX_CADDYFILE_SIZE:
        raise ValueError(
            f"Caddyfile exceeds maximum size of {_MAX_CADDYFILE_SIZE} bytes"
        )
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(ConfigTemplate.compute_checksum, caddyfile),
            timeout=_checksum_timeout_seconds(),
        )
    except asyncio.TimeoutError as exc:
        raise ValueError("Caddyfile checksum calculation timed out") from exc


def _is_checksum_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint_name == "uq_config_templates_checksum":
        return True
    message = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
    return (
        "uq_config_templates_checksum" in message
        or "unique constraint failed: config_templates.checksum" in message
    )


class ConfigTemplateRepository:
    """Repository for ConfigTemplate CRUD operations.

    Write methods flush pending changes but do not commit. Callers own the
    transaction boundary and must commit or roll back explicitly.
    """

    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count(ConfigTemplate.id)))
        return int(result.scalar_one())

    async def list_all(
        self,
        session: AsyncSession,
        *,
        limit: int | None = _DEFAULT_LIST_LIMIT,
        offset: int | None = 0,
    ) -> list[ConfigTemplate]:
        statement = (
            select(ConfigTemplate)
            .options(selectinload(ConfigTemplate.sites))
            .order_by(ConfigTemplate.name.asc())
            .limit(_normalize_limit(limit))
            .offset(_normalize_offset(offset))
        )
        result = await session.execute(statement)
        return list(result.scalars().all())

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

    async def get_by_caddyfile(self, session: AsyncSession, caddyfile: str) -> ConfigTemplate | None:
        checksum = await _compute_checksum(caddyfile)
        return await self.get_by_checksum(session, checksum)

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
        started_at = perf_counter()
        checksum = await _compute_checksum(caddyfile)
        variables_snapshot = _snapshot_variables(variables)
        template = ConfigTemplate(
            name=name,
            description=description,
            caddyfile=caddyfile,
            checksum=checksum,
            variables=copy.deepcopy(variables_snapshot),
        )
        session.add(template)
        try:
            await session.flush()
        except IntegrityError as exc:
            if _is_checksum_conflict(exc):
                raise TemplateAlreadyExistsError(
                    "A template with identical Caddyfile content already exists."
                ) from exc
            raise

        # Create initial revision
        revision = ConfigRevision(
            template_id=template.id,
            version=1,
            caddyfile=caddyfile,
            checksum=checksum,
            variables=copy.deepcopy(variables_snapshot),
            change_summary="Initial creation",
            created_by=created_by,
        )
        session.add(revision)
        await session.flush()

        logger.debug(
            "Created config template %s in %.3fs",
            template.id,
            perf_counter() - started_at,
        )

        return template

    async def update(
        self,
        session: AsyncSession,
        template: ConfigTemplate,
        *,
        name: str | _UnsetType = _UNSET,
        caddyfile: str | _UnsetType = _UNSET,
        description: str | None | _UnsetType = _UNSET,
        variables: dict[str, Any] | _UnsetType = _UNSET,
        change_summary: str | None | _UnsetType = _UNSET,
        updated_by: str | None = None,
    ) -> ConfigTemplate:
        """Update a template inside the caller's transaction.

        ``_lock_template_for_update`` is a best-effort optimization for databases
        that support row locks. Correctness for concurrent writes comes from the
        model-level optimistic ``version_id`` column.

        Passing ``_UNSET`` leaves a field unchanged. Passing ``None`` explicitly
        clears nullable fields such as ``description``.
        """
        started_at = perf_counter()
        template_id = template.id
        await self._lock_template_for_update(session, template_id)

        current_variables = _snapshot_variables(template.variables)
        caddyfile_changed = caddyfile is not _UNSET and caddyfile != template.caddyfile
        variables_changed = False
        variables_snapshot: dict[str, Any] | None = None
        if variables is not _UNSET:
            variables_snapshot = _snapshot_variables(variables)
            variables_changed = variables_snapshot != current_variables

        if name is not _UNSET:
            template.name = name
        if description is not _UNSET:
            template.description = description
        if caddyfile is not _UNSET:
            template.caddyfile = caddyfile
            template.checksum = await _compute_checksum(caddyfile)
        if variables_snapshot is not None:
            template.variables = copy.deepcopy(variables_snapshot)

        try:
            await session.flush()
        except IntegrityError as exc:
            if _is_checksum_conflict(exc):
                raise TemplateAlreadyExistsError(
                    "A template with identical Caddyfile content already exists."
                ) from exc
            raise
        except StaleDataError as exc:
            raise ConcurrentTemplateUpdateError(
                f"Config template {template_id} was modified concurrently"
            ) from exc

        if caddyfile_changed or variables_changed:
            await self._create_revision_with_retry(
                session,
                template=template,
                change_summary=(
                    change_summary if change_summary is not _UNSET else "Configuration updated"
                ),
                updated_by=updated_by,
            )

        logger.debug(
            "Updated config template %s in %.3fs",
            template_id,
            perf_counter() - started_at,
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
        limit: int | None = _DEFAULT_LIST_LIMIT,
        offset: int | None = 0,
    ) -> list[ConfigRevision]:
        statement = (
            select(ConfigRevision)
            .where(ConfigRevision.template_id == template_id)
            .order_by(ConfigRevision.version.desc())
            .limit(_normalize_limit(limit))
            .offset(_normalize_offset(offset))
        )
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
        """Best-effort row lock for backends that support ``FOR UPDATE``.

        Must be called inside the caller-managed transaction. SQLite ignores
        ``FOR UPDATE``, so optimistic locking remains the primary safeguard.
        """
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
        """Create a new revision using a savepoint to preserve the outer transaction.

        The caller owns the surrounding transaction. The nested transaction is
        only used so uniqueness conflicts on ``(template_id, version)`` can be
        retried without discarding the caller's in-flight template update.
        """
        retry_limit = _revision_retry_limit()
        for attempt in range(retry_limit):
            try:
                await self._lock_template_for_update(session, template.id)
                async with session.begin_nested():
                    max_version = await self._get_max_version(session, template.id)
                    revision = ConfigRevision(
                        template_id=template.id,
                        version=max_version + 1,
                        caddyfile=template.caddyfile,
                        checksum=template.checksum,
                        variables=_snapshot_variables(template.variables),
                        change_summary=change_summary,
                        created_by=updated_by,
                    )
                    session.add(revision)
                    await session.flush()
                return
            except IntegrityError as exc:
                if not self._is_revision_version_conflict(exc) or attempt == retry_limit - 1:
                    logger.warning(
                        "Exhausted config template revision retries for template %s after %s attempts",
                        template.id,
                        attempt + 1,
                    )
                    raise
                logger.warning(
                    "Retrying config template revision insert for template %s after version conflict (attempt %s/%s)",
                    template.id,
                    attempt + 1,
                    retry_limit,
                )
                await session.refresh(template)

    @staticmethod
    def _is_revision_version_conflict(exc: IntegrityError) -> bool:
        constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint_name == "uq_config_revisions_template_version":
            return True
        message = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
        return (
            "uq_config_revisions_template_version" in message
            or "config_revisions.template_id, config_revisions.version" in message
            or "unique constraint failed: config_revisions.template_id, config_revisions.version" in message
        )


config_template_repository = ConfigTemplateRepository()
