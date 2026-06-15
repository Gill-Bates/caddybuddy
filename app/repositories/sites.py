#!/usr/bin/env python3
#
# app/repositories/sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.base import utc_now
from app.models.entities import (
    CaddyBuddyState,
    Site,
    _normalize_domain_name,
    _normalize_site_name,
    _normalize_upstream_url,
)
from app.utils.caddyfile import extract_upstream_from_directives, normalize_caddy_directives
from app.utils.domains import split_domain_names


_MAX_LIST_LIMIT = 500
_LEGACY_UPSTREAM_PLACEHOLDER = "http://placeholder.invalid"
_DOMAIN_LOCK_KEY = "site_domain_lock"


class DuplicateSiteError(ValueError):
    """Raised when a site domain collides with an existing row."""


class SiteRepository:
    """Repository for the simplified site model."""

    @staticmethod
    def _domain_names(value: str) -> set[str]:
        return set(split_domain_names(_normalize_domain_name(value)))

    @classmethod
    def _domains_overlap(cls, left: str, right: str) -> bool:
        left_domains = cls._domain_names(left)
        right_domains = cls._domain_names(right)
        return not left_domains.isdisjoint(right_domains)

    @staticmethod
    async def _acquire_domain_lock(session: AsyncSession) -> None:
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            insert_stmt = postgres_insert(CaddyBuddyState)
        elif dialect_name == "sqlite":
            insert_stmt = sqlite_insert(CaddyBuddyState)
        else:
            return

        statement = insert_stmt.values(
            key=_DOMAIN_LOCK_KEY,
            value="1",
            updated_at=utc_now(),
        ).on_conflict_do_update(
            index_elements=[CaddyBuddyState.key],
            set_={
                "value": "1",
                "updated_at": utc_now(),
            },
        )
        await session.execute(statement)

    @staticmethod
    def _validate_enabled_upstream(upstream_url: str, enabled: bool) -> None:
        if enabled and upstream_url == _LEGACY_UPSTREAM_PLACEHOLDER:
            raise ValueError(
                "Enabled sites must define a valid upstream_url in caddy_directives. "
                "The placeholder value is not a valid upstream target."
            )

    @staticmethod
    def _derive_legacy_upstream_url(
        caddy_directives: str,
        *,
        fallback_host: str | None = None,
    ) -> str:
        extracted_upstream = extract_upstream_from_directives(caddy_directives)
        if extracted_upstream is None:
            if fallback_host is not None:
                return _normalize_upstream_url(f"http://{fallback_host}")
            return _LEGACY_UPSTREAM_PLACEHOLDER
        if "://" in extracted_upstream:
            return _normalize_upstream_url(extracted_upstream)
        return _normalize_upstream_url(f"http://{extracted_upstream}")

    @staticmethod
    def _is_duplicate_domain_integrity_error(exc: IntegrityError) -> bool:
        orig = exc.orig
        constraint_name = getattr(getattr(orig, "diag", None), "constraint_name", None) or getattr(
            orig,
            "constraint_name",
            None,
        )
        if constraint_name == "uq_caddy_sites_domain":
            return True

        message = " ".join(
            part.lower()
            for part in (
                str(orig or ""),
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
        requested_domains = self._domain_names(normalized_domain)
        result = await session.execute(select(Site).where(Site.domain == normalized_domain))
        site = result.scalar_one_or_none()
        if site is not None:
            return site

        result = await session.execute(select(Site).order_by(Site.domain.asc()).with_for_update())
        for site in result.scalars().all():
            if not requested_domains.isdisjoint(self._domain_names(site.domain)):
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
        requested_domains = self._domain_names(normalized_domain)
        statement = select(Site.id).where(Site.domain == normalized_domain)
        if exclude_id is not None:
            statement = statement.where(Site.id != exclude_id)
        result = await session.execute(statement.limit(1))
        if result.scalar_one_or_none() is not None:
            return True

        statement = select(Site.id, Site.domain).order_by(Site.domain.asc()).with_for_update()
        if exclude_id is not None:
            statement = statement.where(Site.id != exclude_id)
        result = await session.execute(statement)
        for row in result.all():
            if not requested_domains.isdisjoint(self._domain_names(row.domain)):
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
        await self._acquire_domain_lock(session)
        if await self.domain_exists(session, normalized_domain):
            raise DuplicateSiteError("Site domain already exists.")

        normalized_directives = normalize_caddy_directives(caddy_directives)
        if not normalized_directives:
            raise ValueError("caddy_directives cannot be empty")

        upstream_url = self._derive_legacy_upstream_url(
            normalized_directives,
            fallback_host=split_domain_names(normalized_domain)[0],
        )
        self._validate_enabled_upstream(upstream_url, enabled)
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
            await self._acquire_domain_lock(session)
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
            next_upstream_url = self._derive_legacy_upstream_url(
                normalized_directives,
                fallback_host=split_domain_names(next_domain)[0],
            )

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
