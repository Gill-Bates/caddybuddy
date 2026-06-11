#!/usr/bin/env python3
#
# app/repositories/sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Site, _normalize_domain_name, _normalize_site_name, _normalize_upstream_url
from app.utils.caddyfile import extract_upstream_from_directives, normalize_caddy_directives
from app.utils.domains import split_domain_names


_MAX_LIST_LIMIT = 500
_LEGACY_UPSTREAM_PLACEHOLDER = "http://placeholder.invalid"


class DuplicateSiteError(ValueError):
    """Raised when a site domain collides with an existing row."""


class SiteRepository:
    """Repository for the simplified site model."""

    @staticmethod
    def _domains_overlap(left: str, right: str) -> bool:
        return not set(split_domain_names(left)).isdisjoint(split_domain_names(right))

    @staticmethod
    def _validate_enabled_upstream(upstream_url: str, enabled: bool) -> None:
        if enabled and upstream_url == _LEGACY_UPSTREAM_PLACEHOLDER:
            raise ValueError(
                "Enabled sites must define a valid upstream_url in caddy_directives. "
                "The placeholder value is not a valid upstream target."
            )

    @staticmethod
    def _derive_legacy_upstream_url(caddy_directives: str) -> str:
        extracted_upstream = extract_upstream_from_directives(caddy_directives)
        if extracted_upstream is None:
            return _LEGACY_UPSTREAM_PLACEHOLDER
        if "://" in extracted_upstream:
            return _normalize_upstream_url(extracted_upstream)
        return _normalize_upstream_url(f"http://{extracted_upstream}")

    @staticmethod
    def _is_duplicate_domain_integrity_error(exc: IntegrityError) -> bool:
        message = " ".join(
            part.lower()
            for part in (
                str(exc.orig or ""),
                str(exc.statement or ""),
                str(exc),
            )
            if part
        )
        return (
            "caddy_sites.domain" in message
            or "uq_caddy_sites_domain" in message
        ) and any(token in message for token in ("unique", "duplicate", "constraint"))

    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count()).select_from(Site))
        return int(result.scalar_one())

    async def list_all(
        self,
        session: AsyncSession,
        *,
        limit: int | None = None,
        enabled_only: bool = False,
    ) -> list[Site]:
        statement = select(Site).order_by(Site.site_name.asc(), Site.domain.asc())
        if enabled_only:
            statement = statement.where(Site.enabled.is_(True))
        if limit is not None:
            if limit < 1 or limit > _MAX_LIST_LIMIT:
                raise ValueError(f"Limit must be between 1 and {_MAX_LIST_LIMIT}.")
            statement = statement.limit(limit)
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def get_by_id(
        self,
        session: AsyncSession,
        site_id: int,
        *,
        for_update: bool = False,
    ) -> Site | None:
        statement = select(Site).where(Site.id == site_id)
        if for_update:
            statement = statement.with_for_update()
        result = await session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_domain(self, session: AsyncSession, domain: str) -> Site | None:
        """Get site by a contained domain name. Domain matching is case-insensitive."""
        normalized_domain = _normalize_domain_name(domain)
        result = await session.execute(select(Site).where(Site.domain == normalized_domain))
        site = result.scalar_one_or_none()
        if site is not None:
            return site

        result = await session.execute(select(Site).order_by(Site.domain.asc()))
        for site in result.scalars().all():
            if self._domains_overlap(site.domain, normalized_domain):
                return site
        return None

    async def domain_exists(
        self,
        session: AsyncSession,
        domain: str,
        *,
        exclude_id: int | None = None,
    ) -> bool:
        """Check whether any requested domain is already assigned."""
        normalized_domain = _normalize_domain_name(domain)
        statement = select(Site.id).where(Site.domain == normalized_domain)
        if exclude_id is not None:
            statement = statement.where(Site.id != exclude_id)
        result = await session.execute(statement.limit(1))
        if result.scalar_one_or_none() is not None:
            return True

        statement = select(Site.id, Site.domain).order_by(Site.domain.asc())
        if exclude_id is not None:
            statement = statement.where(Site.id != exclude_id)
        result = await session.execute(statement)
        for row in result.all():
            if self._domains_overlap(row.domain, normalized_domain):
                return True
        return False

    async def create(
        self,
        session: AsyncSession,
        *,
        site_name: str,
        domain: str,
        caddy_directives: str,
        enabled: bool = True,
    ) -> Site:
        normalized_site_name = _normalize_site_name(site_name)
        normalized_domain = _normalize_domain_name(domain)
        if await self.domain_exists(session, normalized_domain):
            raise DuplicateSiteError("Site domain already exists.")

        normalized_directives = normalize_caddy_directives(caddy_directives)
        if not normalized_directives:
            raise ValueError("caddy_directives cannot be empty")

        upstream_url = self._derive_legacy_upstream_url(normalized_directives)
        site = Site(
            site_name=normalized_site_name,
            domain=normalized_domain,
            upstream_url=upstream_url,
            caddy_directives=normalized_directives,
            enabled=enabled,
        )
        session.add(site)
        try:
            await session.flush()
        except IntegrityError as exc:
            if self._is_duplicate_domain_integrity_error(exc):
                raise DuplicateSiteError("Site domain already exists.") from exc
            raise
        return site

    async def update(
        self,
        session: AsyncSession,
        site: Site,
        *,
        site_name: str | None = None,
        domain: str | None = None,
        caddy_directives: str | None = None,
        enabled: bool | None = None,
    ) -> Site:
        next_site_name = site.site_name if site_name is None else _normalize_site_name(site_name)
        next_domain = site.domain
        next_caddy_directives = site.caddy_directives
        next_upstream_url = site.upstream_url
        next_enabled = site.enabled if enabled is None else enabled

        if domain is not None:
            normalized_domain = _normalize_domain_name(domain)
            if await self.domain_exists(
                session,
                normalized_domain,
                exclude_id=getattr(site, "id", None),
            ):
                raise DuplicateSiteError("Site domain already exists.")
            next_domain = normalized_domain
        if caddy_directives is not None:
            normalized_directives = normalize_caddy_directives(caddy_directives)
            if not normalized_directives:
                raise ValueError("caddy_directives cannot be empty")
            next_caddy_directives = normalized_directives
            next_upstream_url = self._derive_legacy_upstream_url(normalized_directives)

        self._validate_enabled_upstream(next_upstream_url, next_enabled)
        site.site_name = next_site_name
        site.domain = next_domain
        site.caddy_directives = next_caddy_directives
        site.upstream_url = next_upstream_url
        site.enabled = next_enabled
        try:
            await session.flush()
        except IntegrityError as exc:
            if self._is_duplicate_domain_integrity_error(exc):
                raise DuplicateSiteError("Site domain already exists.") from exc
            raise
        return site

    async def delete(self, session: AsyncSession, site: Site) -> None:
        await session.delete(site)
        await session.flush()


site_repository = SiteRepository()
