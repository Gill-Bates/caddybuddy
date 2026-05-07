#!/usr/bin/env python3
#
# app/services/audit.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import User
from app.repositories.audit_logs import audit_log_repository


class AuditService:
    async def log_action(
        self,
        session: AsyncSession,
        *,
        action: str,
        resource_type: str,
        request: Request,
        resource_id: str | None = None,
        details: dict | None = None,
        status_code: int | None = None,
        actor: User | None = None,
    ):
        username = actor.username if actor else "anonymous"
        return await audit_log_repository.create(
            session,
            action=action,
            username=username,
            resource_type=resource_type,
            resource_id=resource_id,
            details_json=details or {},
            status_code=status_code,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            user_id=actor.id if actor else None,
        )


audit_service = AuditService()