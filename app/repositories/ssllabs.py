#!/usr/bin/env python3
#
# app/repositories/ssllabs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.entities import Site, SslLabsRankHistory, SslLabsScan, SslLabsTarget
from app.schemas.ssllabs import (
    SSLLABS_ACTIVE_SCAN_STATUSES,
    SSLLABS_TERMINAL_SCAN_STATUSES,
    SslLabsScanStatus,
)
from app.utils.domains import split_domain_names


ACTIVE_SCAN_STATUSES = frozenset(SSLLABS_ACTIVE_SCAN_STATUSES)
TERMINAL_SCAN_STATUSES = frozenset(SSLLABS_TERMINAL_SCAN_STATUSES)
ACTIVE_SCAN_STALE_AFTER = timedelta(hours=2)
_MAX_DUE_TARGET_LIMIT = 500
_TLS_OFF_RE = re.compile(r"(?im)^\s*tls\s+off\s*$")
_AUTO_HTTPS_OFF_RE = re.compile(r"(?im)^\s*auto_https\s+off\s*$")


def _require_aware_datetime(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")
    return value


def active_scan_cutoff(now: datetime) -> datetime:
    now = _require_aware_datetime(now, name="now")
    return now - ACTIVE_SCAN_STALE_AFTER


def site_uses_https(site: Site) -> bool:
    if not getattr(site, "enabled", True):
        return False

    directives = (getattr(site, "caddy_directives", None) or "").strip()
    if not directives:
        return True

    return _TLS_OFF_RE.search(directives) is None and _AUTO_HTTPS_OFF_RE.search(directives) is None


class SslLabsRepository:
    async def _has_active_scan(
        self,
        session: AsyncSession,
        target_id: int,
        *,
        now: datetime,
    ) -> bool:
        return await self.get_active_scan_for_target(session, target_id, now=now) is not None

    async def sync_targets(self, session: AsyncSession, sites: list[Site]) -> list[SslLabsTarget]:
        now = datetime.now(UTC)
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
            if await self._has_active_scan(session, target.id, now=now):
                continue
            await session.delete(target)

        await session.flush()

        refreshed = await session.execute(select(SslLabsTarget).order_by(SslLabsTarget.host.asc()))
        return list(refreshed.scalars().all())

    async def list_targets_with_latest_scans(
        self,
        session: AsyncSession,
    ) -> list[tuple[SslLabsTarget, Site, SslLabsScan | None]]:
        ranked_scans = (
            select(
                SslLabsScan.id.label("scan_id"),
                SslLabsScan.target_id.label("target_id"),
                func.row_number()
                .over(
                    partition_by=SslLabsScan.target_id,
                    order_by=(SslLabsScan.started_at.desc(), SslLabsScan.id.desc()),
                )
                .label("scan_rank"),
            )
            .subquery()
        )
        latest_scan = aliased(SslLabsScan)

        result = await session.execute(
            select(SslLabsTarget, Site, latest_scan)
            .join(Site, Site.id == SslLabsTarget.site_id)
            .outerjoin(
                ranked_scans,
                (ranked_scans.c.target_id == SslLabsTarget.id) & (ranked_scans.c.scan_rank == 1),
            )
            .outerjoin(latest_scan, latest_scan.id == ranked_scans.c.scan_id)
            .order_by(SslLabsTarget.host.asc(), Site.domain.asc())
        )
        return [(target, site, scan) for target, site, scan in result.all()]

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
        effective_now = datetime.now(UTC) if now is None else _require_aware_datetime(now, name="now")
        active_scan = await self.get_active_scan_for_target(session, target.id, now=effective_now)
        if active_scan is not None:
            return None

        try:
            async with session.begin_nested():
                scan = SslLabsScan(
                    target_id=target.id,
                    site_id=target.site_id,
                    host=target.host,
                    status=status,
                )
                session.add(scan)
                await session.flush()
        except IntegrityError:
            return await self.get_active_scan_for_target(session, target.id, now=effective_now)
        return scan

    async def list_completed_scans_since(
        self,
        session: AsyncSession,
        *,
        since: datetime,
    ) -> list[SslLabsScan]:
        """Return ready scans with a grade completed at or after ``since``.

        Used to build the dashboard SSL Labs rank timeseries. Ordered by host then
        completion time so callers can bucket per host chronologically.
        """
        _require_aware_datetime(since, name="since")
        result = await session.execute(
            select(SslLabsScan)
            .where(
                SslLabsScan.status == "ready",
                SslLabsScan.grade.is_not(None),
                SslLabsScan.completed_at.is_not(None),
                SslLabsScan.completed_at >= since,
            )
            .order_by(SslLabsScan.host.asc(), SslLabsScan.completed_at.asc(), SslLabsScan.id.asc())
        )
        return list(result.scalars().all())

    async def record_rank_history(
        self,
        session: AsyncSession,
        *,
        host: str,
        grade: str,
        rank: int,
        recorded_at: datetime,
    ) -> SslLabsRankHistory:
        """Append one daily SSL Labs grade sample to the history table.

        Staged in the caller's transaction; the caller owns the commit.
        """
        _require_aware_datetime(recorded_at, name="recorded_at")
        entry = SslLabsRankHistory(host=host, grade=grade, rank=rank, recorded_at=recorded_at)
        session.add(entry)
        return entry

    async def list_rank_history_since(
        self,
        session: AsyncSession,
        *,
        since: datetime,
    ) -> list[SslLabsRankHistory]:
        """Return rank-history samples recorded at or after ``since``.

        Ordered by host then record time so callers can bucket per host chronologically.
        """
        _require_aware_datetime(since, name="since")
        result = await session.execute(
            select(SslLabsRankHistory)
            .where(SslLabsRankHistory.recorded_at >= since)
            .order_by(
                SslLabsRankHistory.host.asc(),
                SslLabsRankHistory.recorded_at.asc(),
                SslLabsRankHistory.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def prune_rank_history_older_than(
        self,
        session: AsyncSession,
        *,
        cutoff: datetime,
    ) -> int:
        """Delete rank-history samples recorded before ``cutoff``; return rows removed."""
        _require_aware_datetime(cutoff, name="cutoff")
        result = await session.execute(
            delete(SslLabsRankHistory).where(SslLabsRankHistory.recorded_at < cutoff)
        )
        return result.rowcount or 0

    async def list_due_targets(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        limit: int = 25,
    ) -> list[SslLabsTarget]:
        _require_aware_datetime(now, name="now")
        if limit < 1 or limit > _MAX_DUE_TARGET_LIMIT:
            raise ValueError(f"Limit must be between 1 and {_MAX_DUE_TARGET_LIMIT}.")

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
