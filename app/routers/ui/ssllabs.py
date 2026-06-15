#!/usr/bin/env python3
#
# app/routers/ui/ssllabs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.repositories.ssllabs import active_scan_cutoff, ssllabs_repository
from app.services.ssllabs import (
    SslLabsServiceError,
    ssllabs_service,
)
from app.utils.ssllabs import (
    extract_endpoint_details,
    grade_badge_class,
    is_ssllabs_scan_active,
    is_ssllabs_scan_failed,
    next_schedule_time,
    parse_ssllabs_schedule_control,
    status_badge_class,
    validate_ssllabs_host,
)

from ._common import require_admin, require_onboarding_completed, require_user, validated_form


router = APIRouter()
logger = logging.getLogger(__name__)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _normalize_filter_grade(latest_scan, endpoints: list[dict[str, object]]) -> str:
    if latest_scan is None:
        return "not-scanned"

    scan_status = str(getattr(latest_scan, "status", "") or "").strip().lower()
    if getattr(latest_scan, "error_message", None) or is_ssllabs_scan_failed(scan_status):
        return "failed"

    scan_grade = str(getattr(latest_scan, "grade", "") or "").strip().lower()
    if scan_grade:
        return scan_grade

    endpoint_grades = {
        str(endpoint.get("grade") or "").strip().lower()
        for endpoint in endpoints
        if isinstance(endpoint, dict) and str(endpoint.get("grade") or "").strip()
    }
    if len(endpoint_grades) > 1:
        return "mixed"
    if len(endpoint_grades) == 1:
        return endpoint_grades.pop()
    return "not-scanned"


@router.get("/ssl-labs", response_class=HTMLResponse)
async def ssllabs_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")

    onboarding_redirect = await require_onboarding_completed(session)
    if onboarding_redirect is not None:
        return onboarding_redirect

    site_rows: list[dict[str, object]] = []
    site_rows_by_id: dict[int, dict[str, object]] = {}
    now = datetime.now(UTC)
    stale_cutoff = active_scan_cutoff(now)
    target_rows = sorted(
        await ssllabs_repository.list_targets_with_latest_scans(session),
        key=lambda row: (
            str(getattr(row[1], "site_name", getattr(row[1], "domain", ""))).casefold(),
            str(getattr(row[0], "host", "")).casefold(),
        ),
    )
    for target, site, latest_scan in target_rows:
        host_error: str | None = None
        try:
            validate_ssllabs_host(target.host)
        except ValueError as exc:
            host_error = str(exc)

        scan_active = False
        if latest_scan is not None and is_ssllabs_scan_active(latest_scan.status):
            reference_at = _as_utc(latest_scan.next_poll_at or latest_scan.started_at)
            scan_active = reference_at is not None and reference_at >= stale_cutoff

        # Extract endpoint details for IPv4/IPv6 breakdown
        endpoints = []
        if latest_scan is not None:
            endpoints = extract_endpoint_details(getattr(latest_scan, "result_json", None))

        site_row = site_rows_by_id.get(site.id)
        if site_row is None:
            site_row = {"site": site, "domains": []}
            site_rows_by_id[site.id] = site_row
            site_rows.append(site_row)

        scan_report_available = bool(
            latest_scan is not None
            and getattr(latest_scan, "grade", None)
            and getattr(latest_scan, "completed_at", None) is not None
        )
        site_row["domains"].append(
            {
                "target": target,
                "site": site,
                "scan": latest_scan,
                "host_error": host_error,
                "filter_grade": _normalize_filter_grade(latest_scan, endpoints),
                "badge_class": status_badge_class(
                    getattr(latest_scan, "status", None),
                    getattr(latest_scan, "grade", None),
                ),
                "grade_badge_class": grade_badge_class(getattr(latest_scan, "grade", None)),
                "endpoints": endpoints,
                "scan_active": scan_active,
                "scan_report_available": scan_report_available,
            }
        )

    context = {
        "page_title": "SSL Labs",
        "site_rows": site_rows,
    }
    return render_template(request, "ssllabs.html", current_user=current_user, context=context)


@router.post("/ssl-labs/{target_id}/scan")
@limiter.limit("5/minute")
async def start_ssllabs_scan(
    request: Request,
    target_id: int = Path(gt=0),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    onboarding_redirect = await require_onboarding_completed(session)
    if onboarding_redirect is not None:
        return onboarding_redirect

    form = await validated_form(request)
    mode = str(form.get("mode", "cache")).strip().lower()
    if mode not in {"cache", "fresh"}:
        push_flash(request, "danger", "Invalid SSL Labs scan mode.")
        return redirect_to("/ssl-labs")
    force_new = mode == "fresh"

    try:
        result = await ssllabs_service.request_scan(target_id=target_id, force_new=force_new)
    except (SslLabsServiceError, ValueError) as exc:
        push_flash(request, "danger", str(exc))
        return redirect_to("/ssl-labs")

    if result.created:
        push_flash(
            request,
            "success",
            f"SSL Labs scan queued for '{result.host}'.",
        )
    else:
        push_flash(
            request,
            "warning",
            f"An SSL Labs scan is already running for '{result.host}'.",
        )
    return redirect_to("/ssl-labs")


@router.post("/ssl-labs/{target_id}/schedule")
@limiter.limit("20/minute")
async def update_ssllabs_schedule(
    request: Request,
    target_id: int = Path(gt=0),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    onboarding_redirect = await require_onboarding_completed(session)
    if onboarding_redirect is not None:
        return onboarding_redirect

    form = await validated_form(request)
    try:
        frequency = parse_ssllabs_schedule_control(str(form.get("schedule_frequency", "")))
        await ssllabs_service.update_schedule(target_id=target_id, frequency=frequency)
    except (SslLabsServiceError, ValueError) as exc:
        push_flash(request, "danger", str(exc))
        return redirect_to("/ssl-labs")

    if frequency is None:
        push_flash(request, "success", "SSL Labs schedule disabled.")
    else:
        push_flash(request, "success", "SSL Labs schedule enabled (weekly).")
        latest_scan = await ssllabs_repository.get_latest_scan_for_target(session, target_id)
        _completed = _as_utc(getattr(latest_scan, "completed_at", None))
        scan_stale = (
            latest_scan is None
            or _completed is None
            or next_schedule_time(frequency, _completed) <= datetime.now(UTC)
        )
        if scan_stale:
            try:
                await ssllabs_service.request_scan(target_id=target_id, force_new=False)
                push_flash(request, "info", "Scan automatically queued.")
            except (SslLabsServiceError, ValueError) as exc:
                logger.warning("Could not queue initial SSL Labs scan for target %s: %s", target_id, exc)
                push_flash(
                    request,
                    "warning",
                    "Schedule was enabled, but the initial scan could not be queued.",
                )
    return redirect_to("/ssl-labs")
