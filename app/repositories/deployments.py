#!/usr/bin/env python3
#
# app/repositories/deployments.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import CaddyServer, Deployment, DeploymentStatus, Site


class DeploymentRepository:
    """Repository for Deployment CRUD and state machine operations.

    The Deployment entity is the key component of the new architecture:
    - Links Sites to Servers
    - Tracks deployment state machine
    - Stores rendered configuration for audit/rollback
    """

    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count(Deployment.id)))
        return int(result.scalar_one())

    async def list_all(
        self, session: AsyncSession, *, limit: int | None = None
    ) -> list[Deployment]:
        statement = (
            select(Deployment)
            .options(selectinload(Deployment.site), selectinload(Deployment.server))
            .order_by(Deployment.created_at.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await session.execute(statement)
        return list(result.scalars().unique().all())

    async def get_by_id(self, session: AsyncSession, deployment_id: int) -> Deployment | None:
        result = await session.execute(
            select(Deployment)
            .options(
                selectinload(Deployment.site).selectinload(Site.config_template),
                selectinload(Deployment.server),
                selectinload(Deployment.rollback_source),
            )
            .where(Deployment.id == deployment_id)
        )
        return result.scalar_one_or_none()

    async def get_by_site_and_server(
        self, session: AsyncSession, site_id: int, server_id: int
    ) -> list[Deployment]:
        """Get all deployments for a site on a specific server."""
        result = await session.execute(
            select(Deployment)
            .options(selectinload(Deployment.site), selectinload(Deployment.server))
            .where(Deployment.site_id == site_id, Deployment.server_id == server_id)
            .order_by(Deployment.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_active_deployment(
        self, session: AsyncSession, site_id: int, server_id: int
    ) -> Deployment | None:
        """Get the currently deployed version of a site on a server."""
        result = await session.execute(
            select(Deployment)
            .options(selectinload(Deployment.site), selectinload(Deployment.server))
            .where(
                Deployment.site_id == site_id,
                Deployment.server_id == server_id,
                Deployment.status == DeploymentStatus.DEPLOYED,
            )
            .order_by(Deployment.deployed_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_deployments_by_server(
        self,
        session: AsyncSession,
        server_id: int,
        *,
        status: DeploymentStatus | None = None,
    ) -> list[Deployment]:
        """Get all deployments on a specific server."""
        statement = (
            select(Deployment)
            .options(selectinload(Deployment.site).selectinload(Site.config_template))
            .where(Deployment.server_id == server_id)
        )
        if status is not None:
            statement = statement.where(Deployment.status == status)
        statement = statement.order_by(Deployment.created_at.desc())
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def get_deployments_by_site(
        self, session: AsyncSession, site_id: int
    ) -> list[Deployment]:
        """Get all deployments for a specific site."""
        result = await session.execute(
            select(Deployment)
            .options(selectinload(Deployment.server))
            .where(Deployment.site_id == site_id)
            .order_by(Deployment.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_pending_deployments(self, session: AsyncSession) -> list[Deployment]:
        """Get all deployments in PENDING or VALIDATING state."""
        result = await session.execute(
            select(Deployment)
            .options(selectinload(Deployment.site), selectinload(Deployment.server))
            .where(
                Deployment.status.in_([DeploymentStatus.PENDING, DeploymentStatus.VALIDATING])
            )
            .order_by(Deployment.created_at.asc())
        )
        return list(result.scalars().all())

    async def create(
        self,
        session: AsyncSession,
        *,
        site_id: int,
        server_id: int,
        rendered_config: str,
        status: DeploymentStatus = DeploymentStatus.PENDING,
        rollback_deployment_id: int | None = None,
    ) -> Deployment:
        rendered_checksum = hashlib.sha256(rendered_config.encode("utf-8")).hexdigest()
        deployment = Deployment(
            site_id=site_id,
            server_id=server_id,
            rendered_config=rendered_config,
            rendered_checksum=rendered_checksum,
            status=status,
            rollback_deployment_id=rollback_deployment_id,
        )
        session.add(deployment)
        await session.flush()
        return deployment

    async def update_status(
        self,
        session: AsyncSession,
        deployment: Deployment,
        status: DeploymentStatus,
        *,
        validation_output: str | None = None,
        deployment_error: str | None = None,
        deployed_by: str | None = None,
    ) -> Deployment:
        """Update deployment status with appropriate state transitions."""
        deployment.status = status
        if validation_output is not None:
            deployment.validation_output = validation_output
        if deployment_error is not None:
            deployment.deployment_error = deployment_error

        if status == DeploymentStatus.DEPLOYED:
            deployment.mark_deployed(deployed_by)

        await session.flush()
        return deployment

    async def delete(self, session: AsyncSession, deployment: Deployment) -> None:
        await session.delete(deployment)
        await session.flush()

    async def get_deployment_history(
        self,
        session: AsyncSession,
        site_id: int,
        server_id: int,
        *,
        limit: int = 10,
    ) -> list[Deployment]:
        """Get deployment history for rollback selection."""
        result = await session.execute(
            select(Deployment)
            .where(
                Deployment.site_id == site_id,
                Deployment.server_id == server_id,
                Deployment.status == DeploymentStatus.DEPLOYED,
            )
            .order_by(Deployment.deployed_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_servers_for_site(
        self, session: AsyncSession, site_id: int
    ) -> list[CaddyServer]:
        """Get all servers where a site has been deployed."""
        result = await session.execute(
            select(CaddyServer)
            .join(Deployment, Deployment.server_id == CaddyServer.id)
            .where(
                Deployment.site_id == site_id,
                Deployment.status == DeploymentStatus.DEPLOYED,
            )
            .distinct()
        )
        return list(result.scalars().all())

    async def check_config_drift(
        self, session: AsyncSession, deployment_id: int
    ) -> dict:
        """Compare deployed checksum with rendered checksum to detect drift."""
        deployment = await self.get_by_id(session, deployment_id)
        if deployment is None:
            return {"error": "Deployment not found"}

        return {
            "deployment_id": deployment.id,
            "rendered_checksum": deployment.rendered_checksum,
            "deployed_checksum": deployment.deployed_checksum,
            "has_drift": deployment.rendered_checksum != deployment.deployed_checksum,
            "status": deployment.status.value,
        }

    async def get_active_deployments_by_server(
        self, session: AsyncSession, server_id: int
    ) -> list[Deployment]:
        """Get all currently DEPLOYED deployments on a server."""
        return await self.get_deployments_by_server(
            session, server_id, status=DeploymentStatus.DEPLOYED
        )


deployment_repository = DeploymentRepository()
