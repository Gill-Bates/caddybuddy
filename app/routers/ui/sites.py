#!/usr/bin/env python3
#
# app/routers/ui/sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Site management router for domain + per-site Caddy directives."""

from __future__ import annotations

import logging
from pydantic import ValidationError

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.repositories.sites import DuplicateSiteError, site_repository
from app.schemas.caddy import SiteCreateRequest
from app.services.caddy import caddy_service
from app.services.caddyfile_manager import get_baseline_caddyfile, validate_and_deploy_full_caddyfile
from app.services.dashboard import CertificateInfo, get_cached_certificate_info_for_domains, get_certificate_info_for_domains
from app.services.events import publish_resource_event
from app.utils.caddyfile import build_domain_site_preview, extract_site_handler_from_directives
from app.utils.domains import split_domain_names

from ._common import (
    parse_int,
    require_admin,
    require_user,
    validated_form,
)


logger = logging.getLogger(__name__)
router = APIRouter()


def _site_update_requires_deploy(
    site,
    *,
    site_name: str,
    domain: str,
    caddy_directives: str,
    enabled: bool,
) -> bool:
    return any(
        (
            site.domain != domain,
            site.caddy_directives != caddy_directives,
            site.enabled != enabled,
        )
    )


def _primary_domain_name(domain_value: str) -> str | None:
    domain_names = split_domain_names(domain_value)
    return domain_names[0] if domain_names else None


def _serialize_certificate_info(info: CertificateInfo) -> dict[str, object]:
    return {
        "exists": info.exists,
        "valid": info.valid,
        "issued_at": info.issued_at.isoformat() if info.issued_at else None,
        "expires_at": info.expires_at.isoformat() if info.expires_at else None,
        "days_remaining": info.days_remaining,
        "error_message": getattr(info, "error_message", None),
    }


async def _build_site_validation_caddyfile(
    session: AsyncSession,
    *,
    domain: str,
    caddy_directives: str,
) -> str:
    baseline = (await get_baseline_caddyfile(session)).strip()
    site_block = build_domain_site_preview(
        name=domain,
        upstream=None,
        caddy_directives=caddy_directives,
        ssl_enabled=True,
    ).strip()

    if baseline:
        return f"{baseline}\n\n{site_block}"
    return site_block


@router.get("/sites/certificates", response_class=JSONResponse)
async def sites_certificates(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Return certificate information for Sites page rows without blocking initial HTML render."""
    current_user = await require_user(request, session)
    if current_user is None:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)

    sites = await site_repository.list_all(session)
    primary_domains = [
        primary_domain
        for site in sites
        if (primary_domain := _primary_domain_name(site.domain)) is not None
    ]
    cert_info = await get_certificate_info_for_domains(primary_domains)
    return JSONResponse(
        {
            "certificates": {
                domain: _serialize_certificate_info(info)
                for domain, info in cert_info.items()
            }
        }
    )


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

    # Handle invalid site_id
    if site_id and selected_site is None:
        push_flash(request, "danger", "Site not found.")
        return redirect_to("/sites")

    domain_catalog: list[dict[str, object]] = []
    for site in sites:
        for domain_name in split_domain_names(site.domain):
            domain_catalog.append({
                "domain": domain_name,
                "site_id": site.id,
            })

    primary_domains_by_site_id = {
        site.id: primary_domain
        for site in sites
        if (primary_domain := _primary_domain_name(site.domain)) is not None
    }
    handler_by_site_id = {
        site.id: handler
        for site in sites
        if (handler := extract_site_handler_from_directives(site.caddy_directives)) is not None
    }
    cert_info = await get_cached_certificate_info_for_domains(
        list(primary_domains_by_site_id.values()),
        allow_stale=True,
    )

    context = {
        "page_title": "Sites",
        "sites": sites,
        "selected_site": selected_site,
        "domain_catalog": domain_catalog,
        "cert_info": cert_info,
        "primary_domains_by_site_id": primary_domains_by_site_id,
        "handler_by_site_id": handler_by_site_id,
    }
    return render_template(request, "sites.html", current_user=current_user, context=context)


@router.post("/sites")
@limiter.limit("10/minute")
async def save_site(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Create or update a site with validation and deployment."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)

    site_id_raw = str(form.get("site_id", "")).strip()
    site_name = str(form.get("site_name", "")).strip()
    domain = str(form.get("domain", "")).strip()
    caddy_directives = str(form.get("caddy_directives", "")).strip()
    enabled = str(form.get("enabled", "")).strip().lower() in {"1", "true", "on", "yes"}

    try:
        payload = SiteCreateRequest(
            site_name=site_name,
            domain=domain,
            caddy_directives=caddy_directives,
            enabled=enabled,
        )
    except ValidationError as exc:
        push_flash(request, "danger", exc.errors()[0]["msg"])
        return redirect_to(f"/sites/{site_id_raw}" if site_id_raw else "/sites")

    site_name = payload.site_name
    domain = payload.domain
    caddy_directives = payload.caddy_directives
    enabled = payload.enabled

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

            requires_deploy = _site_update_requires_deploy(
                site,
                site_name=site_name,
                domain=domain,
                caddy_directives=caddy_directives,
                enabled=enabled,
            )

            await site_repository.update(
                session,
                site,
                site_name=site_name,
                domain=domain,
                caddy_directives=caddy_directives,
                enabled=enabled,
            )
            await session.commit()

            if requires_deploy:
                try:
                    success, deploy_message = await validate_and_deploy_full_caddyfile(session)
                    await session.commit()
                    if success:
                        push_flash(request, "success", f"Site '{site_name}' saved and deployed.")
                    else:
                        logger.warning("Sync failed after site update id=%s site=%s: %s", site.id, site_name, deploy_message)
                        push_flash(
                            request,
                            "warning",
                            f"Site '{site_name}' saved, but synchronization failed: {deploy_message}",
                        )
                except Exception:
                    logger.exception("Deployment raised unexpectedly after site update id=%s", site.id)
                    push_flash(request, "warning", f"Site '{site_name}' saved, but deployment failed unexpectedly.")
            else:
                push_flash(request, "success", f"Site '{site_name}' saved.")
            await publish_resource_event("site", "updated", str(site.id))
            return redirect_to(f"/sites/{site.id}")

        else:
            # Create new site
            if await site_repository.domain_exists(session, domain):
                push_flash(request, "danger", f"Domain '{domain}' is already assigned to another site.")
                return redirect_to("/sites")

            site = await site_repository.create(
                session,
                site_name=site_name,
                domain=domain,
                caddy_directives=caddy_directives,
                enabled=enabled,
            )
            await session.commit()

            try:
                success, deploy_message = await validate_and_deploy_full_caddyfile(session)
                await session.commit()
                if success:
                    push_flash(request, "success", f"Site '{site_name}' created and deployed.")
                else:
                    logger.warning("Sync failed after site create id=%s site=%s: %s", site.id, site_name, deploy_message)
                    push_flash(
                        request,
                        "warning",
                        f"Site '{site_name}' created, but synchronization failed: {deploy_message}",
                    )
            except Exception:
                logger.exception("Deployment raised unexpectedly after site create id=%s", site.id)
                push_flash(request, "warning", f"Site '{site_name}' created, but deployment failed unexpectedly.")
            await publish_resource_event("site", "created", str(site.id))
            return redirect_to(f"/sites/{site.id}")

    except (DuplicateSiteError, IntegrityError):
        await session.rollback()
        push_flash(request, "danger", f"Domain '{domain}' is already in use.")
        target = f"/sites/{site_id_raw}" if parse_int(site_id_raw) is not None else "/sites"
        return redirect_to(target)


@router.post("/sites/{site_id}/delete")
@limiter.limit("10/minute")
async def delete_site(
    request: Request,
    site_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Delete a site and redeploy configuration."""
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
        await session.commit()

        success, deploy_message = await validate_and_deploy_full_caddyfile(session)
        await session.commit()
        if not success:
            logger.warning("Synchronization after site deletion failed: %s", deploy_message)
            push_flash(
                request,
                "warning",
                f"Site '{domain}' deleted, but synchronization failed: {deploy_message}",
            )
        else:
            push_flash(request, "success", f"Site '{domain}' deleted.")
        await publish_resource_event("site", "deleted", str(site_id))

    except IntegrityError:
        await session.rollback()
        push_flash(
            request,
            "danger",
            "Cannot delete site: related records must be removed first.",
        )
        return redirect_to("/sites")

    return redirect_to("/sites")


@router.post("/sites/validate")
@limiter.limit("20/minute")
async def validate_site(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Validate a site definition without deploying."""
    current_user = await require_user(request, session)
    if current_user is None:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)

    form = await validated_form(request)
    try:
        payload = SiteCreateRequest(
            site_name=str(form.get("site_name", "")).strip(),
            domain=str(form.get("domain", "")).strip(),
            caddy_directives=str(form.get("caddy_directives", "")).strip(),
            enabled=str(form.get("enabled", "")).strip().lower() in {"1", "true", "on", "yes"},
        )
    except ValidationError as exc:
        return JSONResponse({"valid": False, "message": exc.errors()[0]["msg"]})

    test_caddyfile = await _build_site_validation_caddyfile(
        session,
        domain=payload.domain,
        caddy_directives=payload.caddy_directives,
    )
    valid, message = await caddy_service.validate_caddyfile(test_caddyfile)
    if not valid:
        return JSONResponse({"valid": False, "message": message})

    return JSONResponse(
        {
            "valid": True,
            "message": f"Site configuration for '{payload.site_name}' is valid.",
        }
    )
