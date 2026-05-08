#!/usr/bin/env python3
#
# app/services/deployment_engine.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Deployment engine service.

Orchestrates the complete deployment pipeline:
    Template → Render → Deploy

This service coordinates between:
- ConfigRenderer (template rendering)
- CaddyService (Caddyfile adaptation and deployment)
- DeploymentStateMachine (state transitions)
- DeploymentRepository (persistence)

CRITICAL: All deployments go through this service.
Do NOT deploy configurations directly via CaddyService.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    CaddyServer,
    Deployment,
    DeploymentStatus,
    Site,
)
from app.repositories.deployments import deployment_repository
from app.services.caddy import CaddyServiceError, caddy_service
from app.services.config_renderer import config_renderer
from app.services.deployment_state import (
    InvalidStateTransitionError,
    deployment_state_machine,
)
from app.services.events import publish_resource_event


if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class DeploymentResult:
    """Result of a deployment operation."""

    success: bool
    deployment: Deployment
    message: str
    validation_output: str | None = None
    error: str | None = None


class DeploymentError(Exception):
    """Raised when deployment fails."""

    def __init__(self, message: str, deployment: Deployment | None = None) -> None:
        self.deployment = deployment
        super().__init__(message)


class DeploymentEngine:
    """Engine for orchestrating site deployments.

    Implements the deployment pipeline:
    1. Render configuration from template + variables
    2. Deploy to target server
    3. Update deployment status and history

    All state transitions go through DeploymentStateMachine.
    """

    @staticmethod
    def _build_failure_rendered_config(site: Site) -> str:
        template = site.config_template
        if template is None:
            return ""

        render_result = config_renderer.render_site_config(site, template)
        return "" if render_result.has_errors else render_result.rendered

    @staticmethod
    async def _apply_state_transition(
        session: AsyncSession,
        deployment: Deployment,
        target: DeploymentStatus,
        transition: Callable[[], None],
    ) -> None:
        source = deployment.status
        transition()
        try:
            await session.flush()
        except StaleDataError as exc:
            raise InvalidStateTransitionError(source, target) from exc

    async def create_deployment(
        self,
        session: AsyncSession,
        *,
        site: Site,
        server: CaddyServer,
        deployed_by: str | None = None,
    ) -> Deployment:
        """Create a new deployment in PENDING state.

        Args:
            session: Database session
            site: Site to deploy
            server: Target server
            deployed_by: Username initiating deployment

        Returns:
            New Deployment entity in PENDING state
        """
        if not site.enabled:
            raise DeploymentError(f"Site '{site.domain}' is disabled")

        if not server.active:
            raise DeploymentError(f"Server '{server.name}' is not active")

        template = site.config_template
        if template is None:
            raise DeploymentError(f"Site '{site.domain}' has no config template")

        # Render configuration
        render_result = config_renderer.render_site_config(site, template)
        if render_result.has_errors:
            raise DeploymentError(
                f"Configuration rendering failed: {', '.join(render_result.missing_vars)}"
            )

        # Create deployment record
        deployment = await deployment_repository.create(
            session,
            site_id=site.id,
            server_id=server.id,
            rendered_config=render_result.rendered,
            status=DeploymentStatus.PENDING,
        )

        logger.info(
            "Created deployment %d for site '%s' on server '%s'",
            deployment.id,
            site.domain,
            server.name,
        )

        return deployment

    async def execute_deployment(
        self,
        session: AsyncSession,
        deployment: Deployment,
        *,
        deployed_by: str | None = None,
    ) -> DeploymentResult:
        """Execute a deployment.

        Transitions: PENDING/FAILED → DEPLOYING → DEPLOYED/FAILED
        """
        try:
            await self._apply_state_transition(
                session,
                deployment,
                DeploymentStatus.DEPLOYING,
                lambda: deployment_state_machine.start_deployment(deployment),
            )
        except InvalidStateTransitionError as exc:
            return DeploymentResult(
                success=False,
                deployment=deployment,
                message=str(exc),
                error=str(exc),
            )

        # Get server for deployment
        server = deployment.server
        if server is None:
            try:
                await self._apply_state_transition(
                    session,
                    deployment,
                    DeploymentStatus.FAILED,
                    lambda: deployment_state_machine.mark_failed(deployment, "Server not found"),
                )
            except InvalidStateTransitionError as exc:
                return DeploymentResult(
                    success=False,
                    deployment=deployment,
                    message=str(exc),
                    error=str(exc),
                )
            return DeploymentResult(
                success=False,
                deployment=deployment,
                message="Server not found",
                error="Server not found",
            )

        # Convert Caddyfile to JSON for deployment
        try:
            config_payload = await caddy_service.adapt_caddyfile_to_json(deployment.rendered_config)
        except (CaddyServiceError, ValueError) as exc:
            error_msg = str(exc)
            try:
                await self._apply_state_transition(
                    session,
                    deployment,
                    DeploymentStatus.FAILED,
                    lambda: deployment_state_machine.mark_failed(deployment, error_msg),
                )
            except InvalidStateTransitionError as transition_exc:
                return DeploymentResult(
                    success=False,
                    deployment=deployment,
                    message=str(transition_exc),
                    error=str(transition_exc),
                )
            return DeploymentResult(
                success=False,
                deployment=deployment,
                message="Failed to convert configuration",
                error=error_msg,
            )

        # Deploy to server via Caddy Admin API
        try:
            await caddy_service.deploy_config(server, config_payload)
        except (CaddyServiceError, ValueError) as exc:
            error_msg = str(exc)
            try:
                await self._apply_state_transition(
                    session,
                    deployment,
                    DeploymentStatus.FAILED,
                    lambda: deployment_state_machine.mark_failed(deployment, error_msg),
                )
            except InvalidStateTransitionError as transition_exc:
                return DeploymentResult(
                    success=False,
                    deployment=deployment,
                    message=str(transition_exc),
                    error=str(transition_exc),
                )

            logger.error(
                "Deployment %d failed on server '%s': %s",
                deployment.id,
                server.name,
                error_msg,
            )

            return DeploymentResult(
                success=False,
                deployment=deployment,
                message="Deployment to server failed",
                error=error_msg,
            )

        # Success
        try:
            await self._apply_state_transition(
                session,
                deployment,
                DeploymentStatus.DEPLOYED,
                lambda: deployment_state_machine.mark_deployed(deployment, deployed_by),
            )
        except InvalidStateTransitionError as exc:
            return DeploymentResult(
                success=False,
                deployment=deployment,
                message=str(exc),
                error=str(exc),
            )

        logger.info(
            "Deployment %d successfully deployed to server '%s'",
            deployment.id,
            server.name,
        )

        # Publish event
        await publish_resource_event(
            "deployment",
            "deployed",
            resource_id=str(deployment.id),
            details={
                "site_domain": deployment.site.domain if deployment.site else None,
                "server_name": server.name,
                "deployed_by": deployed_by,
            },
        )

        return DeploymentResult(
            success=True,
            deployment=deployment,
            message="Deployment completed successfully",
        )

    async def deploy(
        self,
        session: AsyncSession,
        *,
        site: Site,
        server: CaddyServer,
        deployed_by: str | None = None,
    ) -> DeploymentResult:
        """Full deployment pipeline: create → deploy.

        This is the main entry point for deploying a site.

        Args:
            session: Database session
            site: Site to deploy
            server: Target server
            deployed_by: Username initiating deployment

        Returns:
            DeploymentResult with final status
        """
        # Create deployment
        try:
            deployment = await self.create_deployment(
                session,
                site=site,
                server=server,
                deployed_by=deployed_by,
            )
        except DeploymentError as exc:
            # Create a failed deployment record for audit
            deployment = await deployment_repository.create(
                session,
                site_id=site.id,
                server_id=server.id,
                rendered_config=self._build_failure_rendered_config(site),
                status=DeploymentStatus.FAILED,
            )
            deployment.deployment_error = str(exc)
            await session.flush()

            return DeploymentResult(
                success=False,
                deployment=deployment,
                message=str(exc),
                error=str(exc),
            )

        # Execute deployment
        return await self.execute_deployment(
            session,
            deployment,
            deployed_by=deployed_by,
        )

    async def rollback(
        self,
        session: AsyncSession,
        deployment: Deployment,
        *,
        rolled_back_by: str | None = None,
    ) -> DeploymentResult:
        """Rollback a deployed configuration.

        Creates a new deployment from the previous successful deployment.
        """
        if not deployment_state_machine.can_rollback(deployment.status):
            return DeploymentResult(
                success=False,
                deployment=deployment,
                message=f"Cannot rollback deployment in state '{deployment.status.value}'",
                error="Invalid state for rollback",
            )

        # Find previous successful deployment
        history = await deployment_repository.get_deployment_history(
            session,
            deployment.site_id,
            deployment.server_id,
            limit=2,
        )

        if len(history) < 2:
            return DeploymentResult(
                success=False,
                deployment=deployment,
                message="No previous deployment available for rollback",
                error="No rollback target",
            )

        previous_deployment = history[1]  # Skip current, get previous

        # Create rollback deployment
        rollback_deployment = await deployment_repository.create(
            session,
            site_id=deployment.site_id,
            server_id=deployment.server_id,
            rendered_config=previous_deployment.rendered_config,
            rollback_deployment_id=deployment.id,
        )

        # Mark current deployment as rolled back
        try:
            await self._apply_state_transition(
                session,
                deployment,
                DeploymentStatus.ROLLBACK_PENDING,
                lambda: deployment_state_machine.start_rollback(deployment),
            )
        except InvalidStateTransitionError as exc:
            return DeploymentResult(
                success=False,
                deployment=deployment,
                message=str(exc),
                error=str(exc),
            )

        # Execute rollback deployment
        result = await self.execute_deployment(
            session,
            rollback_deployment,
            deployed_by=rolled_back_by,
        )

        if result.success:
            try:
                await self._apply_state_transition(
                    session,
                    deployment,
                    DeploymentStatus.ROLLED_BACK,
                    lambda: deployment_state_machine.mark_rolled_back(deployment),
                )
            except InvalidStateTransitionError as exc:
                return DeploymentResult(
                    success=False,
                    deployment=deployment,
                    message=str(exc),
                    error=str(exc),
                )

            logger.info(
                "Deployment %d rolled back, new deployment %d",
                deployment.id,
                rollback_deployment.id,
            )
        else:
            try:
                await self._apply_state_transition(
                    session,
                    deployment,
                    DeploymentStatus.FAILED,
                    lambda: deployment_state_machine.mark_failed(
                        deployment,
                        result.error or "Rollback deployment failed",
                    ),
                )
            except InvalidStateTransitionError as exc:
                return DeploymentResult(
                    success=False,
                    deployment=deployment,
                    message=str(exc),
                    error=str(exc),
                )

        return result

    async def retry_deployment(
        self,
        session: AsyncSession,
        deployment: Deployment,
        *,
        deployed_by: str | None = None,
    ) -> DeploymentResult:
        """Retry a failed deployment."""
        if not deployment_state_machine.can_retry(deployment.status):
            return DeploymentResult(
                success=False,
                deployment=deployment,
                message=f"Cannot retry deployment in state '{deployment.status.value}'",
                error="Invalid state for retry",
            )

        try:
            await self._apply_state_transition(
                session,
                deployment,
                DeploymentStatus.PENDING,
                lambda: deployment_state_machine.reset_for_retry(deployment),
            )
        except InvalidStateTransitionError as exc:
            return DeploymentResult(
                success=False,
                deployment=deployment,
                message=str(exc),
                error=str(exc),
            )

        # Run validation
        validation_result = await self.validate_deployment(session, deployment)
        if not validation_result.success:
            return validation_result

        # Execute deployment
        return await self.execute_deployment(
            session,
            deployment,
            deployed_by=deployed_by,
        )


deployment_engine = DeploymentEngine()
