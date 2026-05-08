#!/usr/bin/env python3
#
# app/routers/ui/deployments.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""UI Router for Deployment management.

Deployments are the key entity linking Sites to Servers.
They track the deployment state machine and enable rollback.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.models.entities import DeploymentStatus
from app.repositories.deployments import deployment_repository
from app.repositories.servers import server_repository
from app.repositories.sites import site_repository
from app.services.deployment_engine import deployment_engine
from app.services.deployment_state import deployment_state_machine

from ._common import (
    audit_commit_and_flash,
    parse_int,
    require_admin,
    require_user,
    validated_form,
)


router = APIRouter()


@router.get("/deployments", response_class=HTMLResponse)
@router.get("/deployments/{deployment_id}", response_class=HTMLResponse)
async def deployments_page(
    request: Request,
    deployment_id: int | None = None,
    session: AsyncSession = Depends(get_db_session),
):
    """Render deployments management page."""
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")

    deployments = await deployment_repository.list_all(session, limit=100)
    selected_deployment = await deployment_repository.get_by_id(session, deployment_id) if deployment_id else None

    # Get deployment history if selected
    history = []
    if selected_deployment:
        history = await deployment_repository.get_deployment_history(
            session,
            selected_deployment.site_id,
            selected_deployment.server_id,
            limit=10,
        )

    # Get drift check if deployed
    drift_info = None
    if selected_deployment and selected_deployment.status == DeploymentStatus.DEPLOYED:
        drift_info = await deployment_repository.check_config_drift(session, selected_deployment.id)

    # Status counts for overview
    status_counts = {}
    for d in deployments:
        status = d.status.value
        status_counts[status] = status_counts.get(status, 0) + 1

    context = {
        "page_title": "Deployments",
        "deployments": deployments,
        "selected_deployment": selected_deployment,
        "history": history,
        "drift_info": drift_info,
        "status_counts": status_counts,
        "can_retry": selected_deployment and deployment_state_machine.can_retry(selected_deployment.status),
        "can_rollback": selected_deployment and deployment_state_machine.can_rollback(selected_deployment.status),
    }
    return render_template(request, "deployments.html", current_user=current_user, context=context)


@router.post("/deployments/{deployment_id}/validate")
async def validate_deployment(
    request: Request,
    deployment_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Validate a pending deployment."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    await validated_form(request)

    deployment = await deployment_repository.get_by_id(session, deployment_id)
    if deployment is None:
        push_flash(request, "danger", "Deployment not found.")
        return redirect_to("/deployments")

    result = await deployment_engine.validate_deployment(session, deployment)

    if result.success:
        await audit_commit_and_flash(
            session,
            request,
            action="validate",
            resource_type="deployment",
            resource_id=str(deployment_id),
            actor=current_user,
            flashes=(("success", "Deployment validated successfully."),),
        )
    else:
        await session.commit()
        push_flash(request, "danger", f"Validation failed: {result.error or result.message}")

    return redirect_to(f"/deployments/{deployment_id}")


@router.post("/deployments/{deployment_id}/deploy")
async def execute_deployment(
    request: Request,
    deployment_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Execute a validated deployment."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    await validated_form(request)

    deployment = await deployment_repository.get_by_id(session, deployment_id)
    if deployment is None:
        push_flash(request, "danger", "Deployment not found.")
        return redirect_to("/deployments")

    result = await deployment_engine.execute_deployment(
        session,
        deployment,
        deployed_by=current_user.username,
    )

    if result.success:
        await audit_commit_and_flash(
            session,
            request,
            action="deploy",
            resource_type="deployment",
            resource_id=str(deployment_id),
            actor=current_user,
            flashes=(("success", "Deployment executed successfully."),),
        )
    else:
        await session.commit()
        push_flash(request, "danger", f"Deployment failed: {result.error or result.message}")

    return redirect_to(f"/deployments/{deployment_id}")


@router.post("/deployments/{deployment_id}/retry")
async def retry_deployment(
    request: Request,
    deployment_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Retry a failed deployment."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    await validated_form(request)

    deployment = await deployment_repository.get_by_id(session, deployment_id)
    if deployment is None:
        push_flash(request, "danger", "Deployment not found.")
        return redirect_to("/deployments")

    if not deployment_state_machine.can_retry(deployment.status):
        push_flash(request, "danger", f"Cannot retry deployment in state '{deployment.status.value}'.")
        return redirect_to(f"/deployments/{deployment_id}")

    result = await deployment_engine.retry_deployment(
        session,
        deployment,
        deployed_by=current_user.username,
    )

    if result.success:
        await audit_commit_and_flash(
            session,
            request,
            action="retry",
            resource_type="deployment",
            resource_id=str(deployment_id),
            actor=current_user,
            flashes=(("success", "Deployment retry successful."),),
        )
    else:
        await session.commit()
        push_flash(request, "danger", f"Retry failed: {result.error or result.message}")

    return redirect_to(f"/deployments/{deployment_id}")


@router.post("/deployments/{deployment_id}/rollback")
async def rollback_deployment(
    request: Request,
    deployment_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Rollback a deployed configuration."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    await validated_form(request)

    deployment = await deployment_repository.get_by_id(session, deployment_id)
    if deployment is None:
        push_flash(request, "danger", "Deployment not found.")
        return redirect_to("/deployments")

    if not deployment_state_machine.can_rollback(deployment.status):
        push_flash(request, "danger", f"Cannot rollback deployment in state '{deployment.status.value}'.")
        return redirect_to(f"/deployments/{deployment_id}")

    result = await deployment_engine.rollback(
        session,
        deployment,
        rolled_back_by=current_user.username,
    )

    if result.success:
        await audit_commit_and_flash(
            session,
            request,
            action="rollback",
            resource_type="deployment",
            resource_id=str(deployment_id),
            actor=current_user,
            flashes=(("success", "Rollback completed successfully."),),
        )
    else:
        await session.commit()
        push_flash(request, "danger", f"Rollback failed: {result.error or result.message}")

    return redirect_to(f"/deployments/{deployment_id}")


@router.get("/deployments/{deployment_id}/config")
async def get_deployment_config(
    request: Request,
    deployment_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Get rendered configuration for a deployment."""
    current_user = await require_user(request, session)
    if current_user is None:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)

    deployment = await deployment_repository.get_by_id(session, deployment_id)
    if deployment is None:
        return JSONResponse({"detail": "Deployment not found."}, status_code=404)

    return JSONResponse({
        "deployment_id": deployment.id,
        "rendered_config": deployment.rendered_config,
        "rendered_checksum": deployment.rendered_checksum,
        "status": deployment.status.value,
    })


@router.get("/deployments/by-server/{server_id}", response_class=HTMLResponse)
async def deployments_by_server(
    request: Request,
    server_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """View deployments for a specific server."""
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")

    server = await server_repository.get_by_id(session, server_id)
    if server is None:
        push_flash(request, "danger", "Server not found.")
        return redirect_to("/servers")

    deployments = await deployment_repository.get_deployments_by_server(session, server_id)
    active_deployments = await deployment_repository.get_active_deployments_by_server(session, server_id)

    context = {
        "page_title": f"Deployments - {server.name}",
        "server": server,
        "deployments": deployments,
        "active_deployments": active_deployments,
    }
    return render_template(request, "deployments_by_server.html", current_user=current_user, context=context)
