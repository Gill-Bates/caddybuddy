#!/usr/bin/env python3
#
# app/repositories/ssllabs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import datetime, timedelta
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Site, SslLabsScan, SslLabsTarget
from app.schemas.ssllabs import SslLabsScanStatus
from app.utils.domains import split_domain_names


ACTIVE_SCAN_STATUSES = frozenset({"queued", "starting", "dns", "in_progress", "rate_limited"})
TERMINAL_SCAN_STATUSES = frozenset({"ready", "error", "failed"})
ACTIVE_SCAN_STALE_AFTER = timedelta(hours=2)
_TLS_OFF_RE = re.compile(r"(?im)^\s*tls\s+off\s*$")
_AUTO_HTTPS_OFF_RE = re.compile(r"(?im)^\s*auto_https\s+off\s*$")


def active_scan_cutoff(now: datetime) -> datetime:
    return now - ACTIVE_SCAN_STALE_AFTER


def site_uses_https(site: Site) -> bool:
    if not getattr(site, "enabled", True):
        return False

    directives = (getattr(site, "caddy_directives", None) or "").strip()
    if not directives:
        return True

    return _TLS_OFF_RE.search(directives) is None and _AUTO_HTTPS_OFF_RE.search(directives) is None


class SslLabsRepository:
    async def sync_targets(self, session: AsyncSession, sites: list[Site]) -> list[SslLabsTarget]:
        desired_keys: dict[tuple[int, str], Site] = {}
        for site in sites:
            site_id = getattr(site, "id", None)
            if site_id is None:
                continue
            if not site_uses_https(site):
                continue
            for host in split_domain_names(site.domain):
                desired_keys[(site_id, host)] = site

        result = await session.execute(select(SslLabsTarget))
        existing_targets = list(result.scalars().all())
        existing_by_key = {(target.site_id, target.host): target for target in existing_targets}

        for key in sorted(desired_keys):
            if key in existing_by_key:
                continue
            site_id, host = key
            session.add(SslLabsTarget(site_id=site_id, host=host))

        for key, target in existing_by_key.items():
            if key in desired_keys:
                continue
            await session.delete(target)

        await session.flush()

        refreshed = await session.execute(select(SslLabsTarget).order_by(SslLabsTarget.host.asc()))
        return list(refreshed.scalars().all())

    async def list_targets_with_latest_scans(
        self,
        session: AsyncSession,
    ) -> list[tuple[SslLabsTarget, Site, SslLabsScan | None]]:
        result = await session.execute(
            select(SslLabsTarget, Site)
            .join(Site, Site.id == SslLabsTarget.site_id)
            .order_by(SslLabsTarget.host.asc(), Site.domain.asc())
        )
        rows = list(result.all())
        if not rows:
            return []

        target_ids = [target.id for target, _site in rows]
        scan_result = await session.execute(
            select(SslLabsScan)
            .where(SslLabsScan.target_id.in_(target_ids))
            .order_by(SslLabsScan.target_id.asc(), SslLabsScan.started_at.desc(), SslLabsScan.id.desc())
        )
        latest_by_target: dict[int, SslLabsScan] = {}
        for scan in scan_result.scalars().all():
            latest_by_target.setdefault(scan.target_id, scan)

        return [(target, site, latest_by_target.get(target.id)) for target, site in rows]

    async def get_target_with_site(
        self,
        session: AsyncSession,
        target_id: int,
    ) -> tuple[SslLabsTarget, Site] | None:
        result = await session.execute(
            select(SslLabsTarget, Site)
            .join(Site, Site.id == SslLabsTarget.site_id)
            .where(SslLabsTarget.id == target_id)
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def get_target_by_id(self, session: AsyncSession, target_id: int) -> SslLabsTarget | None:
        result = await session.execute(select(SslLabsTarget).where(SslLabsTarget.id == target_id))
        return result.scalar_one_or_none()

    async def get_scan_by_id(self, session: AsyncSession, scan_id: int) -> SslLabsScan | None:
        result = await session.execute(select(SslLabsScan).where(SslLabsScan.id == scan_id))
        return result.scalar_one_or_none()

    async def get_latest_scan_for_target(
        self,
        session: AsyncSession,
        target_id: int,
    ) -> SslLabsScan | None:
        result = await session.execute(
            select(SslLabsScan)
            .where(SslLabsScan.target_id == target_id)
            .order_by(SslLabsScan.started_at.desc(), SslLabsScan.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_scan_for_target(
        self,
        session: AsyncSession,
        target_id: int,
        *,
        now: datetime | None = None,
    ) -> SslLabsScan | None:
        stale_cutoff = active_scan_cutoff(now) if now is not None else None
        conditions = [
            SslLabsScan.target_id == target_id,
            SslLabsScan.status.in_(tuple(ACTIVE_SCAN_STATUSES)),
        ]
        if stale_cutoff is not None:
            conditions.append(func.coalesce(SslLabsScan.next_poll_at, SslLabsScan.started_at) >= stale_cutoff)

        result = await session.execute(
            select(SslLabsScan)
            .where(*conditions)
            .order_by(SslLabsScan.started_at.desc(), SslLabsScan.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_scan_if_none_active(
        self,
        session: AsyncSession,
        target: SslLabsTarget,
        *,
        status: SslLabsScanStatus = "queued",
        now: datetime | None = None,
    ) -> SslLabsScan | None:
        active_scan = await self.get_active_scan_for_target(session, target.id, now=now)
        if active_scan is not None:
            return None

        scan = SslLabsScan(
            target_id=target.id,
            site_id=target.site_id,
            host=target.host,
            status=status,
        )
        session.add(scan)
        await session.flush()
        return scan

    async def list_due_targets(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        limit: int = 25,
    ) -> list[SslLabsTarget]:
        result = await session.execute(
            select(SslLabsTarget)
            .where(
                SslLabsTarget.schedule_frequency.is_not(None),
                SslLabsTarget.next_scheduled_at.is_not(None),
                SslLabsTarget.next_scheduled_at <= now,
            )
            .order_by(SslLabsTarget.next_scheduled_at.asc(), SslLabsTarget.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())


ssllabs_repository = SslLabsRepository()