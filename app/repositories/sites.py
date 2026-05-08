#!/usr/bin/env python3
#
# app/repositories/sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import Deployment, DeploymentStatus, Site


class SiteRepository:
    """Repository for Site CRUD operations.

    The Site entity has a UNIQUE constraint on the domain field,
    enforced at database level. This is critical for preventing
    duplicate domain assignments.
    """

    _PROTECTED_UPDATE_FIELDS = frozenset({"id", "created_at", "updated_at"})

    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count(Site.id)))
        return int(result.scalar_one())

    async def list_all(self, session: AsyncSession, *, limit: int | None = None) -> list[Site]:
        statement = (
            select(Site)
            .options(selectinload(Site.config_template), selectinload(Site.deployments))
            .order_by(Site.domain.asc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await session.execute(statement)
        return list(result.scalars().unique().all())

    async def get_by_id(self, session: AsyncSession, site_id: int) -> Site | None:
        result = await session.execute(
            select(Site)
            .options(
                selectinload(Site.config_template),
                selectinload(Site.deployments).selectinload(Deployment.server),
            )
            .where(Site.id == site_id)
        )
        return result.scalar_one_or_none()

    async def get_by_domain(self, session: AsyncSession, domain: str) -> Site | None:
        """Get site by domain name. Domain matching is case-insensitive."""
        result = await session.execute(
            select(Site)
            .options(selectinload(Site.config_template), selectinload(Site.deployments))
            .where(func.lower(Site.domain) == domain.lower())
        )
        return result.scalar_one_or_none()

    async def domain_exists(self, session: AsyncSession, domain: str, *, exclude_id: int | None = None) -> bool:
        """Check if a domain already exists (case-insensitive)."""
        statement = select(func.count(Site.id)).where(func.lower(Site.domain) == domain.lower())
        if exclude_id is not None:
            statement = statement.where(Site.id != exclude_id)
        result = await session.execute(statement)
        return (result.scalar_one() or 0) > 0

    async def create(
        self,
        session: AsyncSession,
        *,
        domain: str,
        config_template_id: int,
        enabled: bool = True,
        description: str | None = None,
        variables: dict | None = None,
        ssl_enabled: bool = True,
        ssl_provider: str = "letsencrypt",
    ) -> Site:
        site = Site(
            domain=domain.lower().strip(),
            config_template_id=config_template_id,
            enabled=enabled,
            description=description,
            variables=variables or {},
            ssl_enabled=ssl_enabled,
            ssl_provider=ssl_provider,
        )
        session.add(site)
        await session.flush()
        return site

    async def update(
        self,
        session: AsyncSession,
        site: Site,
        *,
        domain: str | None = None,
        config_template_id: int | None = None,
        enabled: bool | None = None,
        description: str | None = None,
        variables: dict | None = None,
        ssl_enabled: bool | None = None,
        ssl_provider: str | None = None,
    ) -> Site:
        if domain is not None:
            site.domain = domain.lower().strip()
        if config_template_id is not None:
            site.config_template_id = config_template_id
        if enabled is not None:
            site.enabled = enabled
        if description is not None:
            site.description = description
        if variables is not None:
            site.variables = variables
        if ssl_enabled is not None:
            site.ssl_enabled = ssl_enabled
        if ssl_provider is not None:
            site.ssl_provider = ssl_provider
        await session.flush()
        return site

    async def delete(self, session: AsyncSession, site: Site) -> None:
        await session.delete(site)
        await session.flush()

    async def get_sites_by_template(
        self, session: AsyncSession, template_id: int
    ) -> list[Site]:
        """Get all sites using a specific config template."""
        result = await session.execute(
            select(Site)
            .options(selectinload(Site.deployments))
            .where(Site.config_template_id == template_id)
            .order_by(Site.domain.asc())
        )
        return list(result.scalars().all())

    async def get_deployed_sites(
        self, session: AsyncSession, server_id: int
    ) -> list[Site]:
        """Get all sites with active deployments on a specific server."""
        result = await session.execute(
            select(Site)
            .options(selectinload(Site.config_template))
            .join(Site.deployments)
            .where(
                Deployment.server_id == server_id,
                Deployment.status == DeploymentStatus.DEPLOYED,
            )
            .order_by(Site.domain.asc())
        )
        return list(result.scalars().unique().all())

    async def get_enabled_sites(self, session: AsyncSession) -> list[Site]:
        """Get all enabled sites."""
        result = await session.execute(
            select(Site)
            .options(selectinload(Site.config_template), selectinload(Site.deployments))
            .where(Site.enabled.is_(True))
            .order_by(Site.domain.asc())
        )
        return list(result.scalars().unique().all())


site_repository = SiteRepository()
