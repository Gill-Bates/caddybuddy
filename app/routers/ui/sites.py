#!/usr/bin/env python3
#
# app/routers/ui/sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Site management router for domain + per-site Caddy directives."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from pydantic import ValidationError

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.repositories.sites import DuplicateSiteError, site_repository
from app.schemas.caddy import SiteCreateRequest
from app.services.caddy import caddy_service
from app.services.caddyfile_manager import get_baseline_caddyfile, sync_caddy_configuration, validate_and_deploy_full_caddyfile
from app.services.caddyfile_manager import validate_rendered_caddy_configuration
from app.services.dashboard import CertificateInfo, get_cached_certificate_info_for_domains, get_certificate_info_for_domains, invalidate_certificate_cache
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


async def _has_local_certificate(domain: str, certificates_path: Path | None) -> bool:
    normalized_domain = domain.strip().lower()
    if not normalized_domain or certificates_path is None:
        return False

    cert_root = certificates_path.expanduser()

    def scan() -> bool:
        try:
            if not cert_root.is_dir():
                return False
            for cert_path in cert_root.rglob("*.crt"):
                try:
                    if not cert_path.is_file():
                        continue
                except OSError:
                    continue
                if cert_path.parent.name.strip().lower() == normalized_domain:
                    return True
        except (OSError, PermissionError):
            return False
        return False

    return await asyncio.to_thread(scan)


async def _auto_request_certificate_if_missing(
    session: AsyncSession,
    domain: str,
) -> tuple[bool, str | None]:
    settings = get_settings()
    if await _has_local_certificate(domain, settings.caddy_certificates_path):
        return False, None

    await invalidate_certificate_cache(domain)
    sync_result = await sync_caddy_configuration(session, force=True)
    if sync_result.status in {"synced", "no_change"}:
        return True, None
    return False, sync_result.error or "Configuration sync failed while requesting certificate."


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
@limiter.limit("10/minute")
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

            if requires_deploy:
                try:
                    success, deploy_message = await validate_and_deploy_full_caddyfile(session)
                    if not success:
                        await session.rollback()
                        push_flash(request, "danger", f"Site '{site_name}' was not saved: {deploy_message}")
                        return redirect_to(f"/sites/{site.id}")
                except Exception:
                    await session.rollback()
                    logger.exception("Deployment raised unexpectedly after site update id=%s", site.id)
                    push_flash(request, "danger", f"Site '{site_name}' was not saved: deployment failed unexpectedly.")
                    return redirect_to(f"/sites/{site.id}")

                primary_domain = _primary_domain_name(domain)
                if primary_domain:
                    cert_requested, cert_error = await _auto_request_certificate_if_missing(session, primary_domain)
                    if cert_error:
                        push_flash(
                            request,
                            "warning",
                            f"Site '{site_name}' saved and deployed, but automatic certificate request failed: {cert_error}",
                        )
                    elif cert_requested:
                        push_flash(
                            request,
                            "info",
                            f"No local certificate found for '{primary_domain}'. Automatic certificate request triggered.",
                        )

            await session.commit()
            if requires_deploy:
                push_flash(request, "success", f"Site '{site_name}' saved and deployed.")
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

            try:
                success, deploy_message = await validate_and_deploy_full_caddyfile(session)
                if not success:
                    await session.rollback()
                    push_flash(request, "danger", f"Site '{site_name}' was not created: {deploy_message}")
                    return redirect_to("/sites")
            except Exception:
                await session.rollback()
                logger.exception("Deployment raised unexpectedly after site create id=%s", site.id)
                push_flash(request, "danger", f"Site '{site_name}' was not created: deployment failed unexpectedly.")
                return redirect_to("/sites")

            primary_domain = _primary_domain_name(domain)
            if primary_domain:
                cert_requested, cert_error = await _auto_request_certificate_if_missing(session, primary_domain)
                if cert_error:
                    push_flash(
                        request,
                        "warning",
                        f"Site '{site_name}' created and deployed, but automatic certificate request failed: {cert_error}",
                    )
                elif cert_requested:
                    push_flash(
                        request,
                        "info",
                        f"No local certificate found for '{primary_domain}'. Automatic certificate request triggered.",
                    )

            await session.commit()
            push_flash(request, "success", f"Site '{site_name}' created and deployed.")
            await publish_resource_event("site", "created", str(site.id))
            return redirect_to("/sites")

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

        success, deploy_message = await validate_and_deploy_full_caddyfile(session)
        if not success:
            await session.rollback()
            push_flash(request, "danger", f"Site '{domain}' was not deleted: {deploy_message}")
            return redirect_to("/sites")

        await session.commit()
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


@router.post("/sites/{site_id}/renew-certificate")
@limiter.limit("5/minute")
async def renew_certificate(
    request: Request,
    site_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Force certificate renewal for a site by redeploying configuration."""
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    await validated_form(request)

    site = await site_repository.get_by_id(session, site_id)
    if site is None:
        push_flash(request, "danger", "Site not found.")
        return redirect_to("/sites")

    primary_domain = _primary_domain_name(site.domain)
    if not primary_domain:
        push_flash(request, "warning", "No domain configured for certificate renewal.")
        return redirect_to("/sites")

    # Publish renewing event immediately for real-time UI update
    await publish_resource_event("certificate", "renewing", primary_domain, {"site_id": site_id})

    try:
        settings = get_settings()

        validation_result = await validate_rendered_caddy_configuration(session)
        if validation_result.status == "validation_failed":
            error_detail = validation_result.error or "Caddy configuration validation failed."
            await publish_resource_event(
                "certificate",
                "renewal_failed",
                primary_domain,
                {"site_id": site_id, "error": error_detail},
            )
            push_flash(
                request,
                "danger",
                f"Certificate renewal was not triggered: {error_detail}",
            )
            return redirect_to("/sites")

        cert_path = settings.caddy_certificates_path
        cert_root_permission_denied = False
        try:
            cert_root_accessible = (
                cert_path is not None and cert_path.expanduser().exists()
            )
        except PermissionError:
            cert_root_accessible = False
            cert_root_permission_denied = True

        removed_artifacts = await caddy_service.purge_certificate_artifacts(
            primary_domain,
            cert_path,
        )

        if removed_artifacts == 0 and not cert_root_accessible:
            # Storage path not accessible — cannot proceed.
            if cert_root_permission_denied:
                path_hint = (
                    f" Permission denied: {cert_path}. "
                    f"Grant the CaddyBuddy process user read access to that path "
                    f"using group membership or ACL (e.g. setfacl -m u:1000:rx {cert_path}) "
                    f"rather than making the directory world-accessible."
                )
                error_detail = "Permission denied on certificate storage path."
            elif cert_path:
                path_hint = (
                    f" Searched: {cert_path}. If running in Docker, mount the Caddy "
                    f"certificate storage and set CB_CADDY_CERTIFICATES_PATH."
                )
                error_detail = "Certificate storage path not accessible."
            else:
                path_hint = " CB_CADDY_CERTIFICATES_PATH is not configured."
                error_detail = "Certificate storage path not configured."
            await publish_resource_event(
                "certificate",
                "renewal_failed",
                primary_domain,
                {"site_id": site_id, "error": error_detail},
            )
            push_flash(
                request,
                "warning",
                f"Certificate renewal was not triggered for '{primary_domain}': "
                f"the certificate storage is not accessible.{path_hint}",
            )
            return redirect_to("/sites")

        # Storage is accessible. If no artifacts existed, Caddy never issued a
        # certificate for this domain — a forced config sync is enough to
        # request one. If artifacts were removed, the sync causes Caddy to
        # reissue the certificate.
        await invalidate_certificate_cache(primary_domain)

        sync_result = await sync_caddy_configuration(session, force=True)
        await session.commit()
        success = sync_result.status in {"synced", "no_change"}
        deploy_message = sync_result.error or (
            "Configuration deployed successfully" if sync_result.status == "synced" else "Configuration unchanged"
        )

        if success:
            await publish_resource_event("certificate", "renewed", primary_domain, {"site_id": site_id})
            if removed_artifacts > 0:
                push_flash(
                    request,
                    "success",
                    f"Certificate renewal triggered for '{primary_domain}': removed "
                    f"{removed_artifacts} artifact(s) from storage. Caddy will re-issue the certificate.",
                )
            else:
                push_flash(
                    request,
                    "info",
                    f"Configuration synchronized for '{primary_domain}'. "
                    f"No certificate artifacts were found on disk — Caddy may already manage "
                    f"this certificate internally, or acquisition is in progress. "
                    f"Check Caddy logs if the certificate does not appear within a few minutes.",
                )
        else:
            await publish_resource_event("certificate", "renewal_failed", primary_domain, {"site_id": site_id, "error": deploy_message})
            push_flash(
                request,
                "warning",
                f"Certificate renewal could not be triggered: {deploy_message}",
            )
    except Exception:
        logger.exception("Certificate renewal failed for site %s", site_id)
        await publish_resource_event("certificate", "renewal_failed", primary_domain, {"site_id": site_id, "error": "Unexpected error"})
        push_flash(request, "danger", "Certificate renewal failed unexpectedly.")

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
    if current_user.role != "admin":
        return JSONResponse({"detail": "Administrator access is required."}, status_code=403)

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

    # Format directives using Caddy's formatter
    try:
        formatted_directives = await caddy_service.format_site_directives(payload.caddy_directives)
    except Exception:
        formatted_directives = payload.caddy_directives

    return JSONResponse(
        {
            "valid": True,
            "message": f"Site configuration for '{payload.site_name}' is valid.",
            "formatted_caddy_directives": formatted_directives,
        }
    )
