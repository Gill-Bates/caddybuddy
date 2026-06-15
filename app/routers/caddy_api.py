#!/usr/bin/env python3
#
# app/routers/caddy_api.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import get_session_user
from app.models.entities import User
from app.repositories.sites import DuplicateSiteError, site_repository
from app.schemas.caddy import (
    CaddyOnboardResponse,
    CaddyStatusResponse,
    CaddySyncResponse,
    SiteCreateRequest,
    SiteDeleteResponse,
    SiteMutationResponse,
    SiteResponse,
    SiteUpdateRequest,
)
from app.services.caddyfile_manager import (
    CaddySyncResult,
    get_caddy_runtime_status,
    onboard_caddy,
    onboarding_result_should_commit,
    onboarding_succeeded,
    sync_caddy_configuration,
    sync_succeeded,
)
from app.services.events import try_publish_resource_event


router = APIRouter(prefix="/api", tags=["caddy"])
logger = logging.getLogger(__name__)


def _sync_error_status(error_code: str | None) -> int:
    if error_code == "onboarding_required":
        return status.HTTP_409_CONFLICT
    if error_code == "caddy_admin_unavailable":
        return status.HTTP_503_SERVICE_UNAVAILABLE
    return status.HTTP_500_INTERNAL_SERVER_ERROR


async def _run_sync_followup(session: AsyncSession) -> CaddySyncResult:
    try:
        sync_result = await sync_caddy_configuration(session)
    except Exception:
        await session.rollback()
        raise
    if not sync_succeeded(sync_result.status):
        await session.rollback()
        raise HTTPException(
            status_code=_sync_error_status(sync_result.error_code),
            detail=sync_result.error or "Caddy synchronization failed.",
        )
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return sync_result


async def _publish_site_event(action: str, site_id: int) -> None:
    try:
        await try_publish_resource_event("site", action, str(site_id))
    except Exception:
        logger.exception("Failed to publish site event", extra={"action": action, "site_id": site_id})


async def _require_admin_api_user(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> User:
    current_user = await get_session_user(request, session)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return current_user


@router.get("/caddy/status", response_model=CaddyStatusResponse)
@limiter.limit("30/minute")
async def caddy_status(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(_require_admin_api_user),
) -> CaddyStatusResponse:
    # SlowAPI requires the Request parameter in the endpoint signature.
    del request
    status_payload = await get_caddy_runtime_status(session)
    return CaddyStatusResponse(
        managed=status_payload.managed,
        onboarding_required=status_payload.onboarding_required,
        caddyfile_path=status_payload.caddyfile_path,
        caddyfile_marker_present=status_payload.caddyfile_marker_present,
        admin_api_reachable=status_payload.admin_api_reachable,
        last_synced_config_sha256=status_payload.last_synced_config_sha256,
        error=status_payload.error,
    )


@router.post("/caddy/onboard", response_model=CaddyOnboardResponse)
@limiter.limit("5/minute")
async def caddy_onboard(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(_require_admin_api_user),
) -> CaddyOnboardResponse:
    # SlowAPI requires the Request parameter in the endpoint signature.
    del request
    result = await onboard_caddy(session)
    if onboarding_result_should_commit(result.status):
        await session.commit()
    else:
        await session.rollback()

    response_payload = CaddyOnboardResponse(
        status=result.status,
        snapshot_sha256=result.snapshot_sha256,
        synced=result.synced,
        detail=result.error,
    )
    if not sync_succeeded(result.status) and not onboarding_succeeded(result.status):
        response.status_code = _sync_error_status(result.error_code)
    return response_payload


@router.post("/caddy/sync", response_model=CaddySyncResponse)
@limiter.limit("10/minute")
async def caddy_sync(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(_require_admin_api_user),
) -> CaddySyncResponse:
    # SlowAPI requires the Request parameter in the endpoint signature.
    del request
    result = await sync_caddy_configuration(session)
    if sync_succeeded(result.status):
        await session.commit()
    else:
        await session.rollback()

    response_payload = CaddySyncResponse(
        status=result.status,
        config_sha256=result.config_sha256,
        synced=result.synced,
        detail=result.error,
    )
    if not sync_succeeded(result.status):
        response.status_code = _sync_error_status(result.error_code)
    return response_payload


@router.get("/sites", response_model=list[SiteResponse])
@limiter.limit("60/minute")
async def list_sites(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(_require_admin_api_user),
) -> list[SiteResponse]:
    del request
    sites = await site_repository.list_all(session)
    return [SiteResponse.model_validate(site) for site in sites]


@router.post("/sites", response_model=SiteMutationResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_site(
    payload: SiteCreateRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(_require_admin_api_user),
) -> SiteMutationResponse:
    # SlowAPI requires the Request parameter in the endpoint signature.
    del request

    try:
        site = await site_repository.create(
            session,
            site_name=payload.site_name,
            domain=payload.domain,
            caddy_directives=payload.caddy_directives,
            enabled=payload.enabled,
        )
    except DuplicateSiteError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Domain '{payload.domain}' already exists.") from exc
    await session.flush()
    site_response = SiteResponse.model_validate(site)

    sync_result = await _run_sync_followup(session)
    response_payload = SiteMutationResponse(
        status="created",
        site=site_response,
        sync_status=sync_result.status,
        synced=sync_result.synced,
        config_sha256=sync_result.config_sha256,
        sync_error=sync_result.error,
    )
    await _publish_site_event("created", site.id)
    return response_payload


@router.patch("/sites/{site_id}", response_model=SiteMutationResponse)
@limiter.limit("10/minute")
async def update_site(
    site_id: int,
    payload: SiteUpdateRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(_require_admin_api_user),
) -> SiteMutationResponse:
    # SlowAPI requires the Request parameter in the endpoint signature.
    del request
    site = await site_repository.get_by_id(session, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found.")

    new_domain = payload.domain if payload.domain is not None else site.domain
    if await site_repository.domain_exists(session, new_domain, exclude_id=site_id):
        raise HTTPException(status_code=409, detail=f"Domain '{new_domain}' already exists.")

    try:
        site = await site_repository.update(
            session,
            site,
            site_name=payload.site_name,
            domain=payload.domain,
            caddy_directives=payload.caddy_directives,
            enabled=payload.enabled,
        )
    except DuplicateSiteError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.flush()
    site_response = SiteResponse.model_validate(site)

    sync_result = await _run_sync_followup(session)
    response_payload = SiteMutationResponse(
        status="updated",
        site=site_response,
        sync_status=sync_result.status,
        synced=sync_result.synced,
        config_sha256=sync_result.config_sha256,
        sync_error=sync_result.error,
    )
    await _publish_site_event("updated", site.id)
    return response_payload


@router.delete("/sites/{site_id}", response_model=SiteDeleteResponse)
@limiter.limit("10/minute")
async def delete_site(
    site_id: int,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    _current_user: User = Depends(_require_admin_api_user),
) -> SiteDeleteResponse:
    # SlowAPI requires the Request parameter in the endpoint signature.
    del request
    site = await site_repository.get_by_id(session, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found.")

    await site_repository.delete(session, site)
    await session.flush()

    sync_result = await _run_sync_followup(session)
    response_payload = SiteDeleteResponse(
        status="deleted",
        sync_status=sync_result.status,
        config_sha256=sync_result.config_sha256,
        sync_error=sync_result.error,
    )
    await _publish_site_event("deleted", site_id)
    return response_payload
