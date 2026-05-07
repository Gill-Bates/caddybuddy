#!/usr/bin/env python3
#
# app/repositories/audit_logs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import String, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AuditLog


class AuditLogRepository:
    _MAX_LIMIT = 1000
    _DEFAULT_PAGE_SIZE = 50

    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count(AuditLog.id)))
        return int(result.scalar_one())

    async def create(
        self,
        session: AsyncSession,
        *,
        action: str,
        username: str,
        resource_type: str,
        resource_id: str | None,
        details_json: dict[str, Any],
        status_code: int | None,
        ip_address: str | None,
        user_agent: str | None,
        user_id: int | None,
    ) -> AuditLog:
        log_entry = AuditLog(
            action=action,
            username=username,
            resource_type=resource_type,
            resource_id=resource_id,
            details_json=details_json,
            status_code=status_code,
            ip_address=ip_address,
            user_agent=user_agent,
            user_id=user_id,
        )
        session.add(log_entry)
        await session.flush()
        return log_entry

    async def list_recent(
        self,
        session: AsyncSession,
        *,
        limit: int = 100,
        action: str | None = None,
        resource_type: str | None = None,
        username: str | None = None,
    ) -> list[AuditLog]:
        """Return the most recent audit log entries, optionally filtered."""
        effective_limit = max(1, min(limit, self._MAX_LIMIT))
        statement = select(AuditLog).order_by(AuditLog.timestamp.desc())
        if action:
            statement = statement.where(AuditLog.action == action)
        if resource_type:
            statement = statement.where(AuditLog.resource_type == resource_type)
        if username:
            statement = statement.where(AuditLog.username == username)
        statement = statement.limit(effective_limit)
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def list_paginated(
        self,
        session: AsyncSession,
        *,
        offset: int = 0,
        limit: int = 50,
        search: str | None = None,
        username: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[AuditLog], int]:
        """
        Return paginated audit log entries with optional filters.

        Args:
            offset: Number of entries to skip.
            limit: Maximum entries to return.
            search: Substring search across action, username, resource_type, details.
            username: Filter by exact username.
            date_from: Filter entries from this datetime (inclusive).
            date_to: Filter entries up to this datetime (inclusive).

        Returns:
            Tuple of (entries, total_count).
        """
        effective_limit = max(1, min(limit, self._DEFAULT_PAGE_SIZE))
        effective_offset = max(0, offset)

        base_query = select(AuditLog)

        if search:
            search_pattern = f"%{search}%"
            base_query = base_query.where(
                or_(
                    AuditLog.action.ilike(search_pattern),
                    AuditLog.username.ilike(search_pattern),
                    AuditLog.resource_type.ilike(search_pattern),
                    func.cast(AuditLog.details_json, String).ilike(search_pattern),
                )
            )
        if username:
            base_query = base_query.where(AuditLog.username == username)
        if date_from:
            base_query = base_query.where(AuditLog.timestamp >= date_from)
        if date_to:
            base_query = base_query.where(AuditLog.timestamp <= date_to)

        count_query = select(func.count()).select_from(base_query.subquery())
        total_count = (await session.execute(count_query)).scalar_one()

        data_query = (
            base_query.order_by(AuditLog.timestamp.desc())
            .offset(effective_offset)
            .limit(effective_limit)
        )
        entries = list((await session.execute(data_query)).scalars().all())

        return entries, int(total_count)

    async def get_distinct_usernames(self, session: AsyncSession) -> list[str]:
        """Return list of distinct usernames in audit logs."""
        result = await session.execute(
            select(AuditLog.username).distinct().order_by(AuditLog.username)
        )
        return [row[0] for row in result.all() if row[0]]


audit_log_repository = AuditLogRepository()