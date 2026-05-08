#!/usr/bin/env python3
#
# app/routers/ui/templates.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""UI Router for ConfigTemplate management.

ConfigTemplates are reusable Caddy configurations.
They are NOT deployed directly - Sites reference them,
and Deployments render them with site-specific variables.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.repositories.config_templates import config_template_repository
from app.services.config_renderer import config_renderer
from app.services.events import publish_resource_event

from ._common import (
    audit_commit_and_flash,
    parse_int,
    require_admin,
    require_user,
    validated_form,
)


router = APIRouter()


TEMPLATE_EXAMPLE = """# Default Caddyfile
# Variables: {{upstream}}, {{domain}} (auto), {{ssl_enabled}} (auto)

import security_headers
import default_log

reverse_proxy {{upstream}} {
    transport http {
        keepalive 30s
    }
    header_up Host {host}
    header_up X-Real-IP {remote_host}
    header_up X-Forwarded-For {remote_host}
    header_up X-Forwarded-Proto {scheme}
}

encode gzip zstd

header {
    -Server
    -X-Powered-By
}"""


def _read_template_form(form: FormData) -> dict:
    """Extract template fields from form data."""
    return {
        "template_id_raw": str(form.get("template_id", "")).strip(),
        "name": str(form.get("name", "")).strip(),
        "description": str(form.get("description", "")).strip() or None,
        "caddyfile": str(form.get("caddyfile", "")).strip(),
        "change_summary": str(form.get("change_summary", "")).strip() or None,
    }


@router.get("/templates", response_class=HTMLResponse)
@router.get("/templates/{template_id}", response_class=HTMLResponse)
async def templates_page(
    request: Request,
    template_id: int | None = None,
    session: AsyncSession = Depends(get_db_session),
):
    """Render templates management page."""
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")

    templates = await config_template_repository.list_all(session)
    selected_template = await config_template_repository.get_by_id(session, template_id) if template_id else None

    # Get revisions for selected template
    revisions = []
    if selected_template:
        revisions = await config_template_repository.get_revisions(session, selected_template.id, limit=10)

    # Get variable info
    defined_vars = set()
    undefined_vars = set()
    if selected_template:
        defined_vars, undefined_vars = config_renderer.validate_template_variables(selected_template)

    context = {
        "page_title": "Caddyfile",
        "templates": templates,
        "selected_template": selected_template,
        "revisions": revisions,
        "defined_vars": sorted(defined_vars),
        "undefined_vars": sorted(undefined_vars),
        "template_example": TEMPLATE_EXAMPLE,
    }
    return render_template(request, "templates.html", current_user=current_user, context=context)


@router.post("/templates")
async def save_template(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Create or update a config template."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    form_data = _read_template_form(form)

    name = form_data["name"]
    if not name:
        push_flash(request, "danger", "Template name is required.")
        return redirect_to("/templates")

    caddyfile = form_data["caddyfile"]
    if not caddyfile:
        push_flash(request, "danger", "Caddyfile content is required.")
        return redirect_to("/templates")

    template_id_raw = form_data["template_id_raw"]

    try:
        if template_id_raw:
            # Update existing template
            template_id = parse_int(template_id_raw)
            if template_id is None:
                push_flash(request, "danger", "Invalid template ID.")
                return redirect_to("/templates")

            template = await config_template_repository.get_by_id(session, template_id)
            if template is None:
                push_flash(request, "danger", "Template not found.")
                return redirect_to("/templates")

            await config_template_repository.update(
                session,
                template,
                name=name,
                description=form_data["description"],
                caddyfile=caddyfile,
                change_summary=form_data["change_summary"],
                updated_by=current_user.username,
            )

            await audit_commit_and_flash(
                session,
                request,
                action="update",
                resource_type="config_template",
                resource_id=str(template.id),
                actor=current_user,
                flashes=(("success", f"Template '{name}' updated successfully."),),
            )
            return redirect_to(f"/templates/{template.id}")

        else:
            template = await config_template_repository.create(
                session,
                name=name,
                description=form_data["description"],
                caddyfile=caddyfile,
                created_by=current_user.username,
            )

            await audit_commit_and_flash(
                session,
                request,
                action="create",
                resource_type="config_template",
                resource_id=str(template.id),
                actor=current_user,
                flashes=(("success", f"Template '{name}' created successfully."),),
            )
            return redirect_to(f"/templates/{template.id}")

    except IntegrityError:
        await session.rollback()
        push_flash(request, "danger", f"Caddyfile name '{name}' is already in use.")
        return redirect_to("/templates")


@router.post("/templates/{template_id}/delete")
async def delete_template(
    request: Request,
    template_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Delete a config template."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    await validated_form(request)

    template = await config_template_repository.get_by_id(session, template_id)
    if template is None:
        push_flash(request, "danger", "Template not found.")
        return redirect_to("/templates")

    # Check if template is in use by sites
    if template.sites:
        site_names = ", ".join(s.domain for s in template.sites[:3])
        if len(template.sites) > 3:
            site_names += f" and {len(template.sites) - 3} more"
        push_flash(request, "danger", f"Cannot delete template - used by sites: {site_names}")
        return redirect_to(f"/templates/{template_id}")

    name = template.name
    await config_template_repository.delete(session, template)

    await audit_commit_and_flash(
        session,
        request,
        action="delete",
        resource_type="config_template",
        resource_id=str(template_id),
        actor=current_user,
        flashes=(("success", f"Template '{name}' deleted successfully."),),
    )
    return redirect_to("/templates")


@router.get("/templates/{template_id}/revisions/{version}", response_class=HTMLResponse)
async def view_revision(
    request: Request,
    template_id: int,
    version: int,
    session: AsyncSession = Depends(get_db_session),
):
    """View a specific template revision."""
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")

    template = await config_template_repository.get_by_id(session, template_id)
    if template is None:
        push_flash(request, "danger", "Template not found.")
        return redirect_to("/templates")

    revision = await config_template_repository.get_revision(session, template_id, version)
    if revision is None:
        push_flash(request, "danger", f"Revision {version} not found.")
        return redirect_to(f"/templates/{template_id}")

    context = {
        "page_title": f"Caddyfile Revision {version} - {template.name}",
        "template": template,
        "revision": revision,
    }
    return render_template(request, "template_revision.html", current_user=current_user, context=context)
