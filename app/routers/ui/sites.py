#!/usr/bin/env python3
#
# app/routers/ui/sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""UI Router for Site management.

Sites are the business unit:
- One domain (UNIQUE constraint at DB level)
- One configuration template
- Zero or more deployments to servers
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import html
import ipaddress
import json
import os
import re
import ssl
import tempfile
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.repositories.config_templates import config_template_repository
from app.repositories.deployments import deployment_repository
from app.repositories.servers import server_repository
from app.repositories.sites import site_repository
from app.services.config_renderer import ConfigRenderError
from app.services.config_renderer import config_renderer
from app.services.deployment_engine import DeploymentError
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
_TLS_PROBE_TIMEOUT_SECONDS = 3.0
_TLS_PROBE_CONCURRENCY = 5
_SSL_PROVIDERS = ("letsencrypt", "zerossl", "manual", "none")
_ALLOWED_SSL_PROVIDERS = frozenset(_SSL_PROVIDERS)
_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def _deployment_navigation_response(target_path: str) -> HTMLResponse:
    if not target_path.startswith("/"):
        target_path = "/"
    escaped_target = html.escape(target_path, quote=True)
    js_target = json.dumps(target_path)
    content = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\">
    <meta http-equiv=\"refresh\" content=\"0;url={escaped_target}\">
    <title>Deployment complete</title>
</head>
<body>
    <p>Deployment complete. <a href=\"{escaped_target}\">Continue</a>.</p>
    <script>
        window.location.replace({js_target});
    </script>
</body>
</html>
"""
    return HTMLResponse(content=content, status_code=200, headers={"Cache-Control": "no-store"})


async def _fetch_site_certificate_expiry(domain: str) -> datetime | None:
    try:
        ipaddress.ip_address(domain)
        return None
    except ValueError:
        pass

    try:
        async with asyncio.timeout(_TLS_PROBE_TIMEOUT_SECONDS):
            reader, writer = await asyncio.open_connection(
                host=domain,
                port=443,
                ssl=ssl.create_default_context(),
                server_hostname=domain,
            )
    except ssl.SSLCertVerificationError:
        insecure_ssl_context = ssl.create_default_context()
        insecure_ssl_context.check_hostname = False
        insecure_ssl_context.verify_mode = ssl.CERT_NONE
        try:
            async with asyncio.timeout(_TLS_PROBE_TIMEOUT_SECONDS):
                reader, writer = await asyncio.open_connection(
                    host=domain,
                    port=443,
                    ssl=insecure_ssl_context,
                    server_hostname=domain,
                )
        except (TimeoutError, OSError, ssl.SSLError):
            return None
    except (TimeoutError, OSError, ssl.SSLError):
        return None

    try:
        ssl_object = writer.get_extra_info("ssl_object")
        if ssl_object is None:
            return None
        peer_cert = ssl_object.getpeercert()
        not_after = peer_cert.get("notAfter") if isinstance(peer_cert, dict) else None
        if not isinstance(not_after, str) or not not_after:
            peer_cert_der = ssl_object.getpeercert(binary_form=True)
            not_after = await _extract_not_after_from_der_certificate(peer_cert_der)
        if not isinstance(not_after, str) or not not_after:
            return None
        return datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None
    finally:
        writer.close()
        await writer.wait_closed()
        del reader


async def _extract_not_after_from_der_certificate(certificate_der: bytes | None) -> str | None:
    return await asyncio.to_thread(_sync_extract_not_after_from_der_certificate, certificate_der)


def _sync_extract_not_after_from_der_certificate(certificate_der: bytes | None) -> str | None:
    if not certificate_der:
        return None

    pem_certificate = ssl.DER_cert_to_PEM_cert(certificate_der)
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False, encoding="utf-8") as handle:
        handle.write(pem_certificate)
        temp_path = handle.name

    try:
        decoded = ssl._ssl._test_decode_cert(temp_path)
    except (AttributeError, OSError, ValueError, ssl.SSLError):
        return None
    finally:
        with suppress(OSError):
            os.unlink(temp_path)

    not_after = decoded.get("notAfter") if isinstance(decoded, dict) else None
    return not_after if isinstance(not_after, str) and not_after else None


async def _load_site_certificate_expiries(sites: list) -> dict[int, datetime | None]:
    ssl_sites = [site for site in sites if getattr(site, "ssl_enabled", False)]
    if not ssl_sites:
        return {}

    semaphore = asyncio.Semaphore(_TLS_PROBE_CONCURRENCY)

    async def fetch_expiry(site) -> tuple[int, datetime | None]:
        async with semaphore:
            return site.id, await _fetch_site_certificate_expiry(site.domain)

    results = await asyncio.gather(*(fetch_expiry(site) for site in ssl_sites))
    return {site_id: expires_at for site_id, expires_at in results}


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


def _is_valid_domain_name(domain: str) -> bool:
    if not (domain and _DOMAIN_RE.fullmatch(domain)):
        return False
    try:
        ipaddress.ip_address(domain)
        return False
    except ValueError:
        return True


def _site_render_validation_message(
    *,
    domain: str,
    ssl_enabled: bool,
    ssl_provider: str,
    variables: dict[str, str],
    template,
) -> str | None:
    candidate_site = SimpleNamespace(
        domain=domain,
        ssl_enabled=ssl_enabled,
        ssl_provider=ssl_provider,
        variables=variables,
    )
    try:
        config_renderer.render_site_config(candidate_site, template, strict=True)
    except ConfigRenderError as exc:
        if exc.missing_vars == ("upstream",):
            return "This Caddyfile requires an upstream target."
        return f"Configuration template is missing required variables: {', '.join(exc.missing_vars)}"
    return None


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
    templates = await config_template_repository.list_all(session, limit=100)
    servers = await server_repository.list_all(session)
    site_certificate_expiries = await _load_site_certificate_expiries(sites)

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
        "site_certificate_expiries": site_certificate_expiries,
        "ssl_providers": list(_SSL_PROVIDERS),
    }
    return render_template(request, "sites.html", current_user=current_user, context=context)


@router.post("/sites")
@limiter.limit("10/minute")
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
    ssl_provider = form_data["ssl_provider"]
    if not domain:
        push_flash(request, "danger", "Domain name is required.")
        return redirect_to("/sites")
    if len(domain) > _MAX_DOMAIN_LENGTH:
        push_flash(request, "danger", f"Domain names must not exceed {_MAX_DOMAIN_LENGTH} characters.")
        return redirect_to("/sites")
    if not _is_valid_domain_name(domain):
        push_flash(request, "danger", "Enter a valid domain name.")
        return redirect_to("/sites")
    if ssl_provider not in _ALLOWED_SSL_PROVIDERS:
        push_flash(request, "danger", "Invalid SSL provider selected.")
        return redirect_to(f"/sites/{form_data['site_id_raw']}" if form_data["site_id_raw"] else "/sites")

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
    site_variables = _merge_site_variables(None, upstream=form_data["upstream"])
    validation_message = _site_render_validation_message(
        domain=domain,
        ssl_enabled=form_data["ssl_enabled"],
        ssl_provider=ssl_provider,
        variables=site_variables,
        template=template,
    )
    if validation_message is not None:
        push_flash(request, "danger", validation_message)
        return redirect_to(f"/sites/{site_id_raw}" if site_id_raw else "/sites")

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
                variables=site_variables,
                ssl_enabled=form_data["ssl_enabled"],
                ssl_provider=ssl_provider,
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
            await publish_resource_event("site", "updated", str(site.id))
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
                variables=site_variables,
                ssl_enabled=form_data["ssl_enabled"],
                ssl_provider=ssl_provider,
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
            await publish_resource_event("site", "created", str(site.id))
            return redirect_to(f"/sites/{site.id}")

    except IntegrityError:
        await session.rollback()
        push_flash(request, "danger", f"Domain '{domain}' is already in use.")
        return redirect_to("/sites")


@router.post("/sites/{site_id}/delete")
@limiter.limit("10/minute")
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
    try:
        await site_repository.delete(session, site)
    except IntegrityError:
        await session.rollback()
        push_flash(
            request,
            "danger",
            "Cannot delete site: existing deployments or related records must be removed first.",
        )
        return redirect_to("/sites")

    await audit_commit_and_flash(
        session,
        request,
        action="delete",
        resource_type="site",
        resource_id=str(site_id),
        actor=current_user,
        flashes=(("success", f"Site '{domain}' deleted successfully."),),
    )
    await publish_resource_event("site", "deleted", str(site_id))
    return redirect_to("/sites")


@router.post("/sites/{site_id}/deploy")
@limiter.limit("5/minute")
async def deploy_site(
    request: Request,
    site_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Send a site deployment request to the queue with a preselected target server."""
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

    push_flash(
        request,
        "info",
        f"Review the queued deployment for '{site.domain}' on '{server.name}' and confirm it from the queue.",
    )
    return redirect_to(f"/queue?site_id={site.id}&server_id={server.id}")


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
    if ssl_provider not in _ALLOWED_SSL_PROVIDERS:
        return JSONResponse({"preview": "# Invalid SSL provider", "errors": ["Invalid SSL provider selected"]}, status_code=400)

    template = await config_template_repository.get_by_id(session, template_id)
    if template is None:
        return JSONResponse({"preview": "# Template not found", "errors": ["Template not found"]})

    # Create a mock site for preview
    mock_site = SimpleNamespace(
        domain=domain,
        config_template_id=template_id,
        ssl_enabled=ssl_enabled,
        ssl_provider=ssl_provider,
        variables=_merge_site_variables(None, upstream=upstream),
        config_template=template,
    )

    render_result = config_renderer.render_site_config(mock_site, template)

    return JSONResponse({
        "preview": render_result.rendered,
        "errors": list(render_result.missing_vars),
        "warnings": list(render_result.warnings),
    })
