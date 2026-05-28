#!/usr/bin/env python3
#
# app/routers/ui/ssllabs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import UTC, datetime

from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.repositories.ssllabs import ACTIVE_SCAN_STATUSES, active_scan_cutoff, ssllabs_repository
from app.schemas.ssllabs import SslLabsScheduleFrequency
from app.services.ssllabs import (
    SslLabsServiceError,
    ssllabs_service,
)
from app.utils.ssllabs import extract_endpoint_details, grade_badge_class, status_badge_class, validate_ssllabs_host

from ._common import require_admin, require_user, validated_form


router = APIRouter()


def _parse_schedule_frequency(raw_value: str) -> SslLabsScheduleFrequency | None:
    normalized = raw_value.strip().lower()
    if not normalized:
        return None
    if normalized not in {"weekly", "monthly"}:
        raise ValueError("Invalid SSL Labs schedule frequency.")
    return normalized  # type: ignore[return-value]


@router.get("/ssl-labs", response_class=HTMLResponse)
async def ssllabs_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")

    await ssllabs_service.sync_targets(session)
    await session.commit()
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
        if latest_scan is not None and latest_scan.status in ACTIVE_SCAN_STATUSES:
            reference_at = latest_scan.next_poll_at or latest_scan.started_at
            scan_active = reference_at >= stale_cutoff

        # Extract endpoint details for IPv4/IPv6 breakdown
        endpoints = []
        if latest_scan is not None:
            endpoints = extract_endpoint_details(getattr(latest_scan, "result_json", None))

        site_row = site_rows_by_id.get(site.id)
        if site_row is None:
            site_row = {"site": site, "domains": []}
            site_rows_by_id[site.id] = site_row
            site_rows.append(site_row)

        site_row["domains"].append(
            {
                "target": target,
                "site": site,
                "scan": latest_scan,
                "host_error": host_error,
                "badge_class": status_badge_class(
                    getattr(latest_scan, "status", None),
                    getattr(latest_scan, "grade", None),
                ),
                "grade_badge_class": grade_badge_class(getattr(latest_scan, "grade", None)),
                "endpoints": endpoints,
                "scan_active": scan_active,
            }
        )

    context = {
        "page_title": "SSL Labs",
        "site_rows": site_rows,
    }
    return render_template(request, "ssllabs.html", current_user=current_user, context=context)


@router.post("/ssl-labs/{target_id}/scan")
async def start_ssllabs_scan(
    request: Request,
    target_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    mode = str(form.get("mode", "cache")).strip().lower()
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
async def update_ssllabs_schedule(
    request: Request,
    target_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")

    form = await validated_form(request)
    try:
        frequency = _parse_schedule_frequency(str(form.get("schedule_frequency", "")))
        await ssllabs_service.update_schedule(target_id=target_id, frequency=frequency)
    except (SslLabsServiceError, ValueError) as exc:
        push_flash(request, "danger", str(exc))
        return redirect_to("/ssl-labs")

    if frequency is None:
        push_flash(request, "success", "SSL Labs schedule disabled.")
    else:
        label = "weekly" if frequency == "weekly" else "every 30 days"
        push_flash(request, "success", f"SSL Labs schedule saved: {label}.")
    return redirect_to("/ssl-labs")