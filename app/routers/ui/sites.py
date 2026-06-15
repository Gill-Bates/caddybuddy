#!/usr/bin/env python3
#
# app/routers/ui/sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Site management router for domain + per-site Caddy directives."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
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
from app.services.caddyfile_manager import (
    build_site_validation_caddyfile,
    sync_caddy_configuration,
    sync_succeeded,
    validate_and_deploy_full_caddyfile,
)
from app.services.runtime_settings import get_caddy_config
from app.services.certificates import CertificateInfo
from app.services.dashboard import (
    get_cached_certificate_info_for_domains,
    get_certificate_info_for_domains,
    invalidate_certificate_cache,
    has_local_certificate_for_domain_checked,
)
from app.services.renewal import CertificateRenewalService
from app.services.events import publish_resource_event
from app.utils.caddyfile import extract_site_handler_from_directives
from app.utils.domains import split_domain_names

from ._common import (
    parse_int,
    require_admin,
    require_onboarding_completed,
    require_user,
    validated_form,
)


logger = logging.getLogger(__name__)
router = APIRouter()

_CERTIFICATE_STATUS_PRIORITY = {
    "valid": 0,
    "pending": 1,
    "missing": 2,
    "expired": 3,
    "error": 4,
    "remote_check_unavailable": 4,
    "storage_unavailable": 5,
}


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
        "status": getattr(info, "status", "missing"),
        "source": getattr(info, "source", "none"),
        "match_type": getattr(info, "match_type", None),
        "is_wildcard": getattr(info, "is_wildcard", False),
        "covering_name": getattr(info, "covering_name", None),
        "checked_at": info.checked_at.isoformat() if getattr(info, "checked_at", None) else None,
        "diagnostics": list(info.diagnostics) if hasattr(info, "diagnostics") else [],
        "local_artifact_present": getattr(info, "local_artifact_present", False),
        "local_artifact_complete": getattr(info, "local_artifact_complete", False),
    }


def _certificate_priority(info: CertificateInfo | None) -> int:
    if info is None:
        return -1
    if info.valid:
        return _CERTIFICATE_STATUS_PRIORITY["valid"]
    status = getattr(info, "status", "missing")
    if status in _CERTIFICATE_STATUS_PRIORITY:
        return _CERTIFICATE_STATUS_PRIORITY[status]
    return _CERTIFICATE_STATUS_PRIORITY["error"] if getattr(info, "error_message", None) else _CERTIFICATE_STATUS_PRIORITY["missing"]


def _pick_worst_certificate_info(infos: list[CertificateInfo | None]) -> CertificateInfo | None:
    selected: CertificateInfo | None = None
    selected_priority = -1
    for info in infos:
        priority = _certificate_priority(info)
        if priority > selected_priority:
            selected = info
            selected_priority = priority
    return selected


async def _invalidate_certificate_cache_for_domains(domains: list[str]) -> None:
    unique_domains = sorted({domain.lower().strip() for domain in domains if domain and domain.strip()})
    for domain in unique_domains:
        await invalidate_certificate_cache(domain)


async def _has_local_certificate(domain: str, certificates_path: Path | None) -> tuple[bool, bool]:
    return await asyncio.to_thread(has_local_certificate_for_domain_checked, certificates_path, domain)


async def _auto_request_certificate_if_missing(
    session: AsyncSession,
    domain: str,
) -> tuple[bool, str | None]:
    settings = get_settings()
    has_certificate, storage_error = await _has_local_certificate(domain, settings.caddy_certificates_path)
    if storage_error:
        return False, "Certificate storage could not be read while checking local artifacts."
    if has_certificate:
        return False, None

    await invalidate_certificate_cache(domain)
    sync_result = await sync_caddy_configuration(session, force=True)
    if sync_succeeded(sync_result.status):
        return True, None
    return False, sync_result.error or "Configuration sync failed while requesting certificate."


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
    if current_user.role != "admin":
        return JSONResponse({"detail": "Administrator access is required."}, status_code=403)

    sites = await site_repository.list_all(session)
    domain_states: dict[str, tuple[bool, datetime | None]] = {
        domain_name: (site.enabled, getattr(site, "updated_at", None))
        for site in sites
        for domain_name in split_domain_names(site.domain)
    }

    cert_info = await get_certificate_info_for_domains(
        list(domain_states),
        managed_site_states=domain_states,
    )

    renewal_service = CertificateRenewalService(session)
    renewals = {}
    for site in sites:
        plan = await renewal_service.build_plan(
            site,
            cert_info=cert_info.get(_primary_domain_name(site.domain) if site.domain else ""),
            fetch_if_missing=False,
        )
        renewals[site.id] = {
            "mode": plan.mode,
            "reason": plan.reason,
            "requires_confirmation": plan.requires_confirmation,
            "scope_name": getattr(plan, "scope_name", None),
            "scope_type": getattr(plan, "scope_type", "domain"),
            "wait_domains": list(getattr(plan, "wait_domains", ())),
        }

    return JSONResponse(
        {
            "certificates": {
                domain: _serialize_certificate_info(info)
                for domain, info in cert_info.items()
            },
            "renewals": renewals,
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
    if current_user.role != "admin":
        push_flash(request, "danger", "Administrator access is required.")
        return redirect_to("/")

    onboarding_redirect = await require_onboarding_completed(session)
    if onboarding_redirect is not None:
        return onboarding_redirect

    sites = await site_repository.list_all(session)
    selected_site = await site_repository.get_by_id(session, site_id) if site_id else None

    # Handle invalid site_id
    if site_id and selected_site is None:
        push_flash(request, "danger", "Site not found.")
        return redirect_to("/sites")

    site_domains_by_site_id = {
        site.id: split_domain_names(site.domain)
        for site in sites
    }
    domain_catalog: list[dict[str, object]] = []
    for site in sites:
        for domain_name in site_domains_by_site_id.get(site.id, ()):
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
    all_domains = sorted({domain for domains in site_domains_by_site_id.values() for domain in domains})
    cert_info = await get_cached_certificate_info_for_domains(all_domains, allow_stale=True)
    site_certificate_info_by_site_id = {
        site.id: _pick_worst_certificate_info([cert_info.get(domain) for domain in site_domains_by_site_id.get(site.id, ())])
        for site in sites
    }
    renewal_service = CertificateRenewalService(session)
    renewals = {}
    for site in sites:
        plan = await renewal_service.build_plan(
            site,
            cert_info=cert_info.get(primary_domains_by_site_id.get(site.id) or ""),
            fetch_if_missing=False,
        )
        renewals[site.id] = plan

    context = {
        "page_title": "Sites",
        "sites": sites,
        "selected_site": selected_site,
        "domain_catalog": domain_catalog,
        "cert_info": cert_info,
        "site_domains_by_site_id": site_domains_by_site_id,
        "site_certificate_info_by_site_id": site_certificate_info_by_site_id,
        "primary_domains_by_site_id": primary_domains_by_site_id,
        "handler_by_site_id": handler_by_site_id,
        "renewals": renewals,
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
    except Exception:
        await session.rollback()
        logger.exception("Deployment raised unexpectedly after site delete id=%s", site_id)
        push_flash(
            request,
            "danger",
            f"Site '{domain}' was not deleted: deployment failed unexpectedly.",
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

    form = await validated_form(request)

    site = await site_repository.get_by_id(session, site_id)
    if site is None:
        push_flash(request, "danger", "Site not found.")
        return redirect_to("/sites")

    primary_domain = _primary_domain_name(site.domain)
    if not primary_domain:
        push_flash(request, "warning", "No domain configured for certificate renewal.")
        return redirect_to("/sites")

    renewal_service = CertificateRenewalService(session)
    plan = await renewal_service.build_plan(site)
    target_scope = getattr(plan, "scope_name", None) or primary_domain
    plan_payload = {"site_id": site_id, "scope": target_scope, "domain": primary_domain}

    if plan.mode == "unavailable":
        error_msg = f"Certificate renewal is unavailable: {plan.reason}"
        await publish_resource_event("certificate", "renewal_failed", primary_domain, {**plan_payload, "error": error_msg})
        push_flash(request, "danger", error_msg)
        return redirect_to("/sites")
    elif plan.mode == "wildcard_scope_required":
        wildcard_scope = target_scope
        payload = {**plan_payload, "scope": wildcard_scope}
        await publish_resource_event(
            "certificate",
            "wildcard_scope_required",
            primary_domain,
            payload,
        )
        await publish_resource_event(
            "certificate",
            "renewal_failed",
            primary_domain,
            {**payload, "error": f"Wildcard scope renewal is required for {wildcard_scope}."},
        )
        push_flash(
            request,
            "warning",
            f"Certificate renewal for '{primary_domain}' requires the wildcard scope '{wildcard_scope}'.",
        )
        return redirect_to("/sites")
    elif plan.mode == "storage_unavailable":
        error_msg = "Caddy certificate storage is inaccessible."
        await publish_resource_event("certificate", "renewal_failed", primary_domain, {**plan_payload, "error": error_msg})
        push_flash(request, "danger", f"Certificate renewal cannot proceed: {error_msg}")
        return redirect_to("/sites")

    try:
        confirmed = str(form.get("confirmed", "false")).lower() == "true"
        if plan.requires_confirmation and not confirmed:
            push_flash(request, "info", "Confirmation required to proceed with restart.")
            return redirect_to("/sites")

        await publish_resource_event("certificate", "renewing", primary_domain, plan_payload)

        # Sync configuration if it was changed
        sync_result = await sync_caddy_configuration(session, force=False)
        if not sync_succeeded(sync_result.status):
            error_detail = sync_result.error or "Caddy configuration synchronization failed."
            await publish_resource_event(
                "certificate",
                "renewal_failed",
                primary_domain,
                {**plan_payload, "error": error_detail},
            )
            push_flash(
                request,
                "danger",
                f"Certificate renewal was not triggered: {error_detail}",
            )
            return redirect_to("/sites")

        if sync_result.status == "synced":
            await session.commit()

        async def progress(action: str, payload: dict[str, object]) -> None:
            await publish_resource_event(
                "certificate",
                action,
                primary_domain,
                {**plan_payload, **payload},
            )

        success, message = await renewal_service.execute(
            site,
            plan,
            confirmed=confirmed,
            progress=progress,
        )

        if success:
            if plan.mode == "acquisition_sync":
                event_name = "renewal_sync_only"
                flash_category = "info"
            else:
                event_name = "renewed"
                flash_category = "success"
            await publish_resource_event("certificate", event_name, primary_domain, {**plan_payload, "message": message})
            push_flash(request, flash_category, message)
        else:
            await publish_resource_event("certificate", "renewal_failed", primary_domain, {**plan_payload, "error": message})
            push_flash(request, "warning", message)

    except Exception:
        await session.rollback()
        logger.exception("Certificate renewal failed for site %s", site_id)
        await publish_resource_event("certificate", "renewal_failed", primary_domain, {**plan_payload, "error": "Unexpected error"})
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

    test_caddyfile = await build_site_validation_caddyfile(
        session,
        domain=payload.domain,
        caddy_directives=payload.caddy_directives,
    )
    caddy_config = await get_caddy_config(session)
    valid, message = await caddy_service.validate_caddyfile(test_caddyfile, admin_url=caddy_config.admin_url)
    if not valid:
        return JSONResponse({"valid": False, "message": message})

    # Normalize indentation with the local UI formatter.
    try:
        formatted_directives = await caddy_service.format_site_directives(payload.caddy_directives)
    except Exception:
        logger.warning("Local Caddy site directive formatting failed.", exc_info=True)
        formatted_directives = payload.caddy_directives

    return JSONResponse(
        {
            "valid": True,
            "message": f"Site configuration for '{payload.site_name}' is valid.",
            "formatted_caddy_directives": formatted_directives,
        }
    )
