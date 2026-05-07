#!/usr/bin/env python3
#
# app/repositories/audit_logs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AuditLog


class AuditLogRepository:
    _MAX_LIMIT = 1000

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


audit_log_repository = AuditLogRepository()