#!/usr/bin/env python3
#
# app/routers/api.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.dependencies.web import get_session_user
from app.repositories.sites import site_repository
from app.schemas.system import BuildInfoResponse, HealthResponse
from app.services.build_info import get_build_info
from app.services.events import ResourceEvent, SubscriberLimitReachedError, event_bus


router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def healthcheck() -> HealthResponse:
    build_info = get_build_info()
    return HealthResponse(status="ok", app="CaddyBuddy", version=build_info["version"])


@router.get("/build-info", response_model=BuildInfoResponse)
async def build_info() -> BuildInfoResponse:
    return BuildInfoResponse(**get_build_info())


async def _event_stream(events: AsyncIterator[ResourceEvent]) -> AsyncIterator[str]:
    """Generate SSE-formatted event stream."""
    async for event in events:
        yield f"data: {event.to_json()}\n\n"


@router.get("/events", response_class=StreamingResponse)
async def subscribe_events() -> StreamingResponse:
    """
    Server-Sent Events endpoint for real-time resource updates.

    Clients connect via EventSource and receive JSON payloads when
    servers, configs, sites, or other resources change.
    """
    try:
        events = event_bus.subscribe()
    except SubscriberLimitReachedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    return StreamingResponse(
        _event_stream(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/queue/count")
async def queue_count(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, int]:
    """Return count of sites pending deployment for authenticated users."""
    user = await get_session_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    count = await site_repository.count_pending_deployment(session)
    return {"count": count}