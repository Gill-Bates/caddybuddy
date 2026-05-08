#!/usr/bin/env python3
#
# app/services/server_status_monitor.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.servers import server_repository
from app.services.caddy import CaddyServiceError, caddy_service
from app.services.events import publish_resource_event


logger = logging.getLogger(__name__)

SERVER_STATUS_POLL_INTERVAL_SECONDS = 30.0


async def _probe_server_status(server) -> str:
    try:
        await caddy_service.test_connection(server)
    except (CaddyServiceError, ValueError):
        return "offline"
    return "online"


async def probe_server_statuses_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    changed_servers: list[tuple[int, str, str]] = []

    async with session_factory() as session:
        servers = await server_repository.list_all(session)
        for server in servers:
            previous_status = server.status
            current_status = await _probe_server_status(server)
            if current_status == previous_status:
                continue
            server.status = current_status
            changed_servers.append((server.id, previous_status, current_status))

        if not changed_servers:
            return

        await session.commit()

    for server_id, previous_status, current_status in changed_servers:
        await publish_resource_event(
            "server",
            "updated",
            str(server_id),
            details={
                "previous_status": previous_status,
                "status": current_status,
            },
        )


async def run_server_status_monitor(
    stop_event: asyncio.Event,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    interval_seconds: float = SERVER_STATUS_POLL_INTERVAL_SECONDS,
) -> None:
    while not stop_event.is_set():
        try:
            await probe_server_statuses_once(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background server status probe failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue