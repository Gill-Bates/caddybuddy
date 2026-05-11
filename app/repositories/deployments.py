#!/usr/bin/env python3
#
# app/repositories/deployments.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import CaddyServer, Deployment, DeploymentStatus, Site


_DEFAULT_DEPLOYMENT_LIST_LIMIT = 100
_DEFAULT_SERVER_LIST_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ConfigDriftResult:
    deployment_id: int
    rendered_checksum: str
    deployed_checksum: str | None
    has_drift: bool
    status: str


def _get_deployment_state_machine():
    from app.services.deployment_state import deployment_state_machine

    return deployment_state_machine


class DeploymentRepository:
    """Repository for Deployment CRUD and state machine operations.

    The Deployment entity is the key component of the new architecture:
    - Links Sites to Servers
    - Tracks deployment state machine
    - Stores rendered configuration for audit/rollback

    Write methods flush to the session but do NOT commit.
    Callers own the transaction boundary and must commit explicitly.
    """

    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count(Deployment.id)))
        return int(result.scalar_one())

    async def list_all(
        self,
        session: AsyncSession,
        *,
        limit: int = _DEFAULT_DEPLOYMENT_LIST_LIMIT,
        offset: int = 0,
    ) -> list[Deployment]:
        statement = (
            select(Deployment)
            .options(selectinload(Deployment.site), selectinload(Deployment.server))
            .order_by(Deployment.created_at.desc())
        )
        statement = statement.limit(limit).offset(offset)
        result = await session.execute(statement)
        return list(result.scalars().all())

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
        self,
        session: AsyncSession,
        site_id: int,
        server_id: int,
        *,
        limit: int = _DEFAULT_DEPLOYMENT_LIST_LIMIT,
        offset: int = 0,
    ) -> list[Deployment]:
        """Get all deployments for a site on a specific server."""
        statement = (
            select(Deployment)
            .options(selectinload(Deployment.site), selectinload(Deployment.server))
            .where(Deployment.site_id == site_id, Deployment.server_id == server_id)
            .order_by(Deployment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(statement)
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
        limit: int = _DEFAULT_DEPLOYMENT_LIST_LIMIT,
        offset: int = 0,
    ) -> list[Deployment]:
        """Get all deployments on a specific server."""
        statement = (
            select(Deployment)
            .options(selectinload(Deployment.site).selectinload(Site.config_template))
            .where(Deployment.server_id == server_id)
        )
        if status is not None:
            statement = statement.where(Deployment.status == status)
        statement = statement.order_by(Deployment.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def get_deployments_by_site(
        self,
        session: AsyncSession,
        site_id: int,
        *,
        limit: int = _DEFAULT_DEPLOYMENT_LIST_LIMIT,
        offset: int = 0,
    ) -> list[Deployment]:
        """Get all deployments for a specific site."""
        statement = (
            select(Deployment)
            .options(selectinload(Deployment.server))
            .where(Deployment.site_id == site_id)
            .order_by(Deployment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def get_pending_deployments(
        self,
        session: AsyncSession,
        *,
        limit: int = _DEFAULT_DEPLOYMENT_LIST_LIMIT,
        offset: int = 0,
    ) -> list[Deployment]:
        """Get all deployments in PENDING or VALIDATING state."""
        statement = (
            select(Deployment)
            .options(selectinload(Deployment.site), selectinload(Deployment.server))
            .where(
                Deployment.status.in_([DeploymentStatus.PENDING, DeploymentStatus.VALIDATING])
            )
            .order_by(Deployment.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(statement)
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
        deployed_by: str | None = None,
    ) -> Deployment:
        if status == DeploymentStatus.DEPLOYED:
            raise ValueError("Deployments cannot be created directly in DEPLOYED status")

        rendered_checksum = hashlib.sha256(rendered_config.encode("utf-8")).hexdigest()
        deployment = Deployment(
            site_id=site_id,
            server_id=server_id,
            rendered_config=rendered_config,
            rendered_checksum=rendered_checksum,
            status=status,
            rollback_deployment_id=rollback_deployment_id,
            deployed_by=deployed_by,
        )
        session.add(deployment)
        await session.flush()
        return deployment

    async def create_imported(
        self,
        session: AsyncSession,
        *,
        site_id: int,
        server_id: int,
        rendered_config: str,
        deployed_by: str | None = None,
    ) -> Deployment:
        """Create a deployment record for a site that is already live on the server.

        Unlike ``create``, this sets the deployment directly to DEPLOYED status
        with matching rendered/deployed checksums so the site does NOT appear in
        the deployment queue.
        """
        rendered_checksum = hashlib.sha256(rendered_config.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        deployment = Deployment(
            site_id=site_id,
            server_id=server_id,
            rendered_config=rendered_config,
            rendered_checksum=rendered_checksum,
            deployed_checksum=rendered_checksum,
            status=DeploymentStatus.DEPLOYED,
            deployed_at=now,
            deployed_by=deployed_by,
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
        deployment_state_machine = _get_deployment_state_machine()

        if status == DeploymentStatus.VALIDATING:
            deployment_state_machine.start_validation(deployment)
        elif status == DeploymentStatus.VALID:
            deployment_state_machine.mark_valid(deployment, validation_output)
        elif status == DeploymentStatus.INVALID:
            deployment_state_machine.mark_invalid(
                deployment,
                deployment_error or validation_output or "Deployment validation failed",
            )
        elif status == DeploymentStatus.DEPLOYING:
            deployment_state_machine.start_deployment(deployment)
        elif status == DeploymentStatus.DEPLOYED:
            deployment_state_machine.mark_deployed(deployment, deployed_by)
        elif status == DeploymentStatus.FAILED:
            deployment_state_machine.mark_failed(
                deployment,
                deployment_error or "Deployment failed",
            )
        elif status == DeploymentStatus.ROLLBACK_PENDING:
            deployment_state_machine.start_rollback(deployment)
        elif status == DeploymentStatus.ROLLED_BACK:
            deployment_state_machine.mark_rolled_back(deployment)
        elif status == DeploymentStatus.PENDING:
            deployment_state_machine.reset_for_retry(deployment)

        if validation_output is not None:
            deployment.validation_output = validation_output
        if deployment_error is not None:
            deployment.deployment_error = deployment_error

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
        self,
        session: AsyncSession,
        site_id: int,
        *,
        limit: int = _DEFAULT_SERVER_LIST_LIMIT,
        offset: int = 0,
    ) -> list[CaddyServer]:
        """Get all servers where a site has been deployed."""
        statement = (
            select(CaddyServer)
            .join(Deployment, Deployment.server_id == CaddyServer.id)
            .where(
                Deployment.site_id == site_id,
                Deployment.status == DeploymentStatus.DEPLOYED,
            )
            .distinct()
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def check_config_drift(
        self, session: AsyncSession, deployment_id: int
    ) -> ConfigDriftResult:
        """Compare deployed checksum with rendered checksum to detect drift."""
        deployment = await self.get_by_id(session, deployment_id)
        if deployment is None:
            raise ValueError(f"Deployment {deployment_id} not found")

        return ConfigDriftResult(
            deployment_id=deployment.id,
            rendered_checksum=deployment.rendered_checksum,
            deployed_checksum=deployment.deployed_checksum,
            has_drift=deployment.rendered_checksum != deployment.deployed_checksum,
            status=deployment.status.value,
        )

    async def get_active_deployments_by_server(
        self, session: AsyncSession, server_id: int
    ) -> list[Deployment]:
        """Get all currently DEPLOYED deployments on a server."""
        return await self.get_deployments_by_server(
            session, server_id, status=DeploymentStatus.DEPLOYED
        )


deployment_repository = DeploymentRepository()
