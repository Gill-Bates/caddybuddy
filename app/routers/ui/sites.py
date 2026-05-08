#!/usr/bin/env python3
#
# app/routers/ui/sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""UI Router for Site management.

Sites are the fachliche Einheit (business unit):
- One domain (UNIQUE constraint at DB level)
- One configuration template
- Zero or more deployments to servers
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.repositories.config_templates import config_template_repository
from app.repositories.deployments import deployment_repository
from app.repositories.servers import server_repository
from app.repositories.sites import site_repository
from app.services.config_renderer import config_renderer
from app.services.deployment_engine import deployment_engine
from app.services.events import publish_resource_event

from ._common import (
    audit_commit_and_flash,
    parse_int,
    require_admin,
    require_user,
    validated_form,
)


router = APIRouter()

_MAX_DOMAIN_LENGTH = 253


def _read_site_form(form: FormData) -> dict:
    """Extract site fields from form data."""
    return {
        "site_id_raw": str(form.get("site_id", "")).strip(),
        "domain": str(form.get("domain", "")).strip().lower(),
        "config_template_id": parse_int(form.get("config_template_id")),
        "enabled": form.get("enabled") == "on",
        "description": str(form.get("description", "")).strip() or None,
        "upstream": str(form.get("upstream", "")).strip() or None,
        "ssl_enabled": form.get("ssl_enabled") == "on",
        "ssl_provider": str(form.get("ssl_provider", "letsencrypt")).strip() or "letsencrypt",
    }


def _merge_site_variables(existing: dict | None, *, upstream: str | None) -> dict[str, str]:
    variables = {
        str(key): str(value)
        for key, value in (existing or {}).items()
        if key != "upstream" and value is not None
    }
    if upstream is not None:
        variables["upstream"] = upstream
    return variables


@router.get("/sites", response_class=HTMLResponse)
@router.get("/sites/{site_id}", response_class=HTMLResponse)
async def sites_page(
    request: Request,
    site_id: int | None = None,
    session: AsyncSession = Depends(get_db_session),
):
    """Render sites management page."""
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")

    sites = await site_repository.list_all(session)
    selected_site = await site_repository.get_by_id(session, site_id) if site_id else None
    templates = await config_template_repository.list_all(session)
    servers = await server_repository.list_all(session)

    # Get deployment info for selected site
    deployments = []
    if selected_site:
        deployments = await deployment_repository.get_deployments_by_site(session, selected_site.id)

    # Generate preview for selected site
    preview = None
    if selected_site and selected_site.config_template:
        render_result = config_renderer.render_site_config(
            selected_site, selected_site.config_template
        )
        preview = render_result.rendered

    context = {
        "page_title": "Sites",
        "sites": sites,
        "selected_site": selected_site,
        "templates": templates,
        "servers": servers,
        "deployments": deployments,
        "site_preview": preview,
        "ssl_providers": ["letsencrypt", "zerossl", "manual", "none"],
    }
    return render_template(request, "sites.html", current_user=current_user, context=context)


@router.post("/sites")
async def save_site(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Create or update a site."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    form_data = _read_site_form(form)

    domain = form_data["domain"]
    if not domain:
        push_flash(request, "danger", "Domain name is required.")
        return redirect_to("/sites")
    if len(domain) > _MAX_DOMAIN_LENGTH:
        push_flash(request, "danger", f"Domain names must not exceed {_MAX_DOMAIN_LENGTH} characters.")
        return redirect_to("/sites")

    template_id = form_data["config_template_id"]
    if template_id is None:
        push_flash(request, "danger", "Configuration template is required.")
        return redirect_to("/sites")

    # Verify template exists
    template = await config_template_repository.get_by_id(session, template_id)
    if template is None:
        push_flash(request, "danger", "Selected configuration template not found.")
        return redirect_to("/sites")

    site_id_raw = form_data["site_id_raw"]

    try:
        if site_id_raw:
            # Update existing site
            site_id = parse_int(site_id_raw)
            if site_id is None:
                push_flash(request, "danger", "Invalid site ID.")
                return redirect_to("/sites")

            site = await site_repository.get_by_id(session, site_id)
            if site is None:
                push_flash(request, "danger", "Site not found.")
                return redirect_to("/sites")

            # Check domain uniqueness (excluding current site)
            if await site_repository.domain_exists(session, domain, exclude_id=site_id):
                push_flash(request, "danger", f"Domain '{domain}' is already assigned to another site.")
                return redirect_to(f"/sites/{site_id}")

            await site_repository.update(
                session,
                site,
                domain=domain,
                config_template_id=template_id,
                enabled=form_data["enabled"],
                description=form_data["description"],
                variables=_merge_site_variables(site.variables, upstream=form_data["upstream"]),
                ssl_enabled=form_data["ssl_enabled"],
                ssl_provider=form_data["ssl_provider"],
            )

            await audit_commit_and_flash(
                session,
                request,
                action="update",
                resource_type="site",
                resource_id=str(site.id),
                actor=current_user,
                flashes=(("success", f"Site '{domain}' updated successfully."),),
            )
            return redirect_to(f"/sites/{site.id}")

        else:
            # Create new site
            if await site_repository.domain_exists(session, domain):
                push_flash(request, "danger", f"Domain '{domain}' is already assigned to another site.")
                return redirect_to("/sites")

            site = await site_repository.create(
                session,
                domain=domain,
                config_template_id=template_id,
                enabled=form_data["enabled"],
                description=form_data["description"],
                variables=_merge_site_variables(None, upstream=form_data["upstream"]),
                ssl_enabled=form_data["ssl_enabled"],
                ssl_provider=form_data["ssl_provider"],
            )

            await audit_commit_and_flash(
                session,
                request,
                action="create",
                resource_type="site",
                resource_id=str(site.id),
                actor=current_user,
                flashes=(("success", f"Site '{domain}' created successfully."),),
            )
            return redirect_to(f"/sites/{site.id}")

    except IntegrityError:
        await session.rollback()
        push_flash(request, "danger", f"Domain '{domain}' is already in use.")
        return redirect_to("/sites")


@router.post("/sites/{site_id}/delete")
async def delete_site(
    request: Request,
    site_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Delete a site."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    await validated_form(request)

    site = await site_repository.get_by_id(session, site_id)
    if site is None:
        push_flash(request, "danger", "Site not found.")
        return redirect_to("/sites")

    domain = site.domain
    await site_repository.delete(session, site)

    await audit_commit_and_flash(
        session,
        request,
        action="delete",
        resource_type="site",
        resource_id=str(site_id),
        actor=current_user,
        flashes=(("success", f"Site '{domain}' deleted successfully."),),
    )
    return redirect_to("/sites")


@router.post("/sites/{site_id}/deploy")
async def deploy_site(
    request: Request,
    site_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Deploy a site to a server."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    server_id = parse_int(form.get("server_id"))

    if server_id is None:
        push_flash(request, "danger", "Target server is required.")
        return redirect_to(f"/sites/{site_id}")

    site = await site_repository.get_by_id(session, site_id)
    if site is None:
        push_flash(request, "danger", "Site not found.")
        return redirect_to("/sites")

    server = await server_repository.get_by_id(session, server_id)
    if server is None:
        push_flash(request, "danger", "Target server not found.")
        return redirect_to(f"/sites/{site_id}")

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
            flashes=(("success", f"Site '{site.domain}' deployed to '{server.name}' successfully."),),
        )
    else:
        await session.commit()
        push_flash(request, "danger", f"Deployment failed: {result.error or result.message}")

    return redirect_to(f"/sites/{site_id}")


@router.post("/sites/preview")
async def preview_site(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Generate site configuration preview."""
    current_user = await require_user(request, session)
    if current_user is None:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)

    form = await validated_form(request)
    template_id = parse_int(form.get("config_template_id"))
    domain = str(form.get("domain", "example.com")).strip().lower() or "example.com"
    upstream = str(form.get("upstream", "")).strip() or None
    ssl_enabled = form.get("ssl_enabled") == "on"
    ssl_provider = str(form.get("ssl_provider", "letsencrypt")).strip()

    if template_id is None:
        return JSONResponse({"preview": "# Select a configuration template", "errors": []})

    template = await config_template_repository.get_by_id(session, template_id)
    if template is None:
        return JSONResponse({"preview": "# Template not found", "errors": ["Template not found"]})

    # Create a mock site for preview
    from app.models.entities import Site
    mock_site = Site(
        domain=domain,
        config_template_id=template_id,
        ssl_enabled=ssl_enabled,
        ssl_provider=ssl_provider,
        variables=_merge_site_variables(None, upstream=upstream),
    )
    mock_site.config_template = template

    render_result = config_renderer.render_site_config(mock_site, template)

    return JSONResponse({
        "preview": render_result.rendered,
        "errors": list(render_result.missing_vars),
        "warnings": list(render_result.warnings),
    })
