#!/usr/bin/env python3
#
# app/routers/api.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.dependencies.web import get_session_user
from app.models.entities import User
from app.schemas.system import BuildInfoResponse, CaddyStatusResponse, HealthResponse
from app.services.build_info import get_build_info
from app.services.caddyfile_manager import get_caddy_runtime_status
from app.services.dashboard import get_caddy_status
from app.services.events import ResourceEvent, SubscriberLimitReachedError, event_bus


router = APIRouter(prefix="/api/v1", tags=["system"])
_SSE_HEARTBEAT_SECONDS = 25


async def _require_api_user(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> User:
    current_user = await get_session_user(request, session)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return current_user


def _format_sse_data(payload: str) -> str:
    lines = payload.splitlines() or [""]
    return "".join(f"data: {line}\n" for line in lines) + "\n"


@router.get("/health", response_model=HealthResponse)
async def healthcheck() -> HealthResponse:
    build_info = get_build_info()
    return HealthResponse(status="ok", app="CaddyBuddy", version=build_info["version"])


@router.get("/ready", response_model=HealthResponse)
async def readiness(
    session: AsyncSession = Depends(get_db_session),
) -> HealthResponse:
    build_info = get_build_info()
    runtime_status = await get_caddy_runtime_status(session)
    if runtime_status.error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=runtime_status.error,
        )
    if runtime_status.onboarding_required:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Caddy onboarding is required.",
        )
    if not runtime_status.admin_api_reachable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Caddy Admin API unavailable.",
        )
    return HealthResponse(status="ok", app="CaddyBuddy", version=build_info["version"])


@router.get("/build-info", response_model=BuildInfoResponse)
async def build_info() -> BuildInfoResponse:
    return BuildInfoResponse(**get_build_info())


@router.get("/caddy/status", response_model=CaddyStatusResponse)
async def caddy_status(
    _current_user: User = Depends(_require_api_user),
) -> CaddyStatusResponse:
    """Get current Caddy service status for dashboard badge refresh."""
    metrics = await get_caddy_status()
    return CaddyStatusResponse(
        running=metrics.status.lower() == "running",
        status=metrics.status,
        uptime=metrics.uptime,
        version=metrics.version,
    )


async def _event_stream(events: AsyncIterator[ResourceEvent]) -> AsyncIterator[str]:
    """Generate SSE-formatted event stream."""
    iterator = events.__aiter__()
    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    iterator.__anext__(),
                    timeout=_SSE_HEARTBEAT_SECONDS,
                )
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            except StopAsyncIteration:
                return
            yield _format_sse_data(event.to_json())
    finally:
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            with suppress(Exception):
                await aclose()


@router.get("/events", response_class=StreamingResponse)
async def subscribe_events(
    _current_user: User = Depends(_require_api_user),
) -> StreamingResponse:
    """
    Server-Sent Events endpoint for real-time resource updates.

    Clients connect via EventSource and receive JSON payloads when
    sites or other resources change.
    """
    try:
        events = event_bus.subscribe()
    except SubscriberLimitReachedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    return StreamingResponse(
        _event_stream(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )