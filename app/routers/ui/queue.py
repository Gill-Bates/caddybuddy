#!/usr/bin/env python3
#
# app/routers/ui/queue.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""UI Router for deployment queue management.

The queue shows all sites that have changes pending deployment:
- Sites that were modified since their last successful deployment
- Sites that have never been deployed

From here, users can deploy changes to all or individual sites.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.config.limiter import limiter
from app.dependencies.web import ensure_csrf_token, push_flash, redirect_to, render_template
from app.models.entities import DeploymentStatus, Site
from app.repositories.servers import server_repository
from app.repositories.sites import site_repository
from app.services.deployment_engine import DeploymentError, deployment_engine
from app.services.events import publish_resource_event

from ._common import (
    audit_commit_and_flash,
    parse_int,
    require_admin,
    require_user,
    validated_form,
)


router = APIRouter()


async def _deploy_site_from_form(
    request: Request,
    session: AsyncSession,
    *,
    site_id: int | None,
):
    """Deploy a single site selected from the queue UI."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    if site_id is None:
        site_id = parse_int(form.get("site_id"))
    server_id = parse_int(form.get("server_id"))

    if site_id is None:
        push_flash(request, "danger", "Please select a site to deploy.")
        return redirect_to("/queue")

    if server_id is None:
        push_flash(request, "danger", "Please select a target server.")
        return redirect_to("/queue")

    site = await site_repository.get_by_id(session, site_id, for_update=True)
    if site is None:
        push_flash(request, "danger", "Site not found.")
        return redirect_to("/queue")

    server = await server_repository.get_by_id(session, server_id)
    if server is None:
        push_flash(request, "danger", "Server not found.")
        return redirect_to("/queue")

    try:
        result = await deployment_engine.deploy(
            session,
            site=site,
            server=server,
            deployed_by=current_user.username,
        )

        if result.success:
            await audit_commit_and_flash(
                session,
                request,
                action="deploy",
                resource_type="site",
                resource_id=str(site_id),
                actor=current_user,
                flashes=(("success", f"Site {site.domain} deployed successfully."),),
            )
        else:
            await session.rollback()
            push_flash(request, "danger", f"Deployment failed: {result.error or result.message}")

    except DeploymentError as err:
        await session.rollback()
        push_flash(request, "danger", f"Deployment error: {err}")

    return redirect_to("/queue")


def _get_latest_deployment(site: Site) -> dict | None:
    """Get latest deployed deployment info for a site."""
    deployed = [
        d for d in (site.deployments or [])
        if d.status == DeploymentStatus.DEPLOYED
    ]
    if not deployed:
        return None
    latest = max(deployed, key=lambda d: d.deployed_at or d.created_at)
    return {
        "server_name": latest.server.name if latest.server else "Unknown",
        "deployed_at": latest.deployed_at,
        "deployed_by": latest.deployed_by,
    }


@router.get("/queue", response_class=HTMLResponse)
async def queue_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Render deployment queue page showing sites with pending changes."""
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")

    pending_sites = await site_repository.list_pending_deployment(session)
    servers = await server_repository.list_all(session, active_only=True)

    # Enrich sites with deployment info
    sites_info = []
    for site in pending_sites:
        latest = _get_latest_deployment(site)
        sites_info.append({
            "site": site,
            "latest_deployment": latest,
            "is_new": latest is None,
        })

    context = {
        "page_title": "Deployment Queue",
        "sites_info": sites_info,
        "pending_count": len(pending_sites),
        "servers": servers,
        "has_servers": len(servers) > 0,
        "csrf_token": ensure_csrf_token(request),
    }
    return render_template(request, "queue.html", current_user=current_user, context=context)


@router.post("/queue/deploy")
@limiter.limit("5/minute")
async def deploy_selected_site(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Deploy a single site selected from the shared queue form."""
    return await _deploy_site_from_form(request, session, site_id=None)


@router.post("/queue/deploy/{site_id}")
@limiter.limit("5/minute")
async def deploy_single_site(
    request: Request,
    site_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Deploy a single site from the queue."""
    return await _deploy_site_from_form(request, session, site_id=site_id)


@router.post("/queue/deploy-all")
@limiter.limit("5/minute")
async def deploy_all_pending(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Deploy all pending sites to the selected server."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    server_id = parse_int(form.get("server_id"))

    if server_id is None:
        push_flash(request, "danger", "Please select a target server.")
        return redirect_to("/queue")

    server = await server_repository.get_by_id(session, server_id)
    if server is None:
        push_flash(request, "danger", "Server not found.")
        return redirect_to("/queue")

    pending_sites = await site_repository.list_pending_deployment(session)
    if not pending_sites:
        push_flash(request, "info", "No pending deployments.")
        return redirect_to("/queue")

    success_count = 0
    error_count = 0
    errors: list[str] = []
    pending_site_refs = [(site.id, site.domain) for site in pending_sites]

    for site_id, site_domain in pending_site_refs:
        try:
            site = await site_repository.get_by_id(session, site_id, for_update=True)
            if site is None:
                error_count += 1
                errors.append(f"{site_domain}: Site not found")
                continue

            result = await deployment_engine.deploy(
                session,
                site=site,
                server=server,
                deployed_by=current_user.username,
            )
            if result.success:
                await session.commit()
                success_count += 1
            else:
                await session.rollback()
                error_count += 1
                if len(errors) < 50:
                    errors.append(f"{site_domain}: {result.error or result.message}")
        except DeploymentError as err:
            await session.rollback()
            error_count += 1
            if len(errors) < 50:
                errors.append(f"{site_domain}: {err}")

    # Finding 2: Refresh the actor object after batch commits/rollbacks
    # to avoid DetachedInstanceError when audit logging.
    await session.refresh(current_user)

    await audit_commit_and_flash(
        session,
        request,
        action="deploy_batch",
        resource_type="queue",
        resource_id=None,
        actor=current_user,
        details={
            "success_count": success_count,
            "error_count": error_count,
            "errors": errors,
            "truncated": error_count > len(errors),
        },
        flashes=(),
    )

    if error_count == 0:
        push_flash(request, "success", f"All {success_count} sites deployed successfully.")
    elif success_count > 0:
        push_flash(request, "warning", f"Deployed {success_count} sites, {error_count} failed.")
    else:
        push_flash(request, "danger", f"All {error_count} deployments failed.")

    for error_message in errors[:5]:
        push_flash(request, "danger", error_message)
    return redirect_to("/queue")
