#!/usr/bin/env python3
#
# app/services/events.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""
Simple in-memory event bus for broadcasting resource changes to connected clients.

Uses Server-Sent Events (SSE) for push notifications. Clients subscribe via
the /api/v1/events endpoint and receive JSON payloads when resources change.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal


logger = logging.getLogger(__name__)

ResourceType = Literal["server", "config", "site", "user", "api_key", "audit_log", "deployment"]
EventAction = Literal["created", "updated", "deleted", "deployed"]


class SubscriberLimitReachedError(RuntimeError):
    """Raised when the SSE subscriber limit has been reached."""


@dataclass(frozen=True, slots=True)
class ResourceEvent:
    """Immutable event representing a resource change."""

    resource_type: ResourceType
    action: EventAction
    resource_id: str | None = None
    details: dict[str, object] | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_json(self) -> str:
        return json.dumps({
            "type": self.resource_type,
            "action": self.action,
            "id": self.resource_id,
            "details": self.details or {},
            "ts": self.timestamp,
        })


class EventBus:
    """
    Broadcast events to all connected SSE clients.

    Subscribers are tracked via one asyncio.Queue per client. Slow subscribers
    are actively terminated so their blocked generator tasks can exit cleanly.
    """

    def __init__(self, max_subscribers: int = 100) -> None:
        self._subscribers: set[asyncio.Queue[ResourceEvent | None]] = set()
        self._max_subscribers = max_subscribers

    @staticmethod
    def _close_queue(queue: asyncio.Queue[ResourceEvent | None]) -> None:
        """Drain a queue and enqueue the shutdown sentinel to unblock waiters."""
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        queue.put_nowait(None)

    def subscribe(self) -> AsyncIterator[ResourceEvent]:
        """
        Yield events as they are published.

        The generator exits when the client disconnects or the bus is shut down.
        """
        if len(self._subscribers) >= self._max_subscribers:
            logger.warning("Max SSE subscribers reached, rejecting new connection")
            raise SubscriberLimitReachedError("Max SSE subscribers reached")

        queue: asyncio.Queue[ResourceEvent | None] = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)

        async def _iterator() -> AsyncIterator[ResourceEvent]:
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield event
            finally:
                self._subscribers.discard(queue)

        return _iterator()

    async def publish(self, event: ResourceEvent) -> None:
        """Broadcast an event to all connected subscribers."""
        dead_queues: list[asyncio.Queue[ResourceEvent | None]] = []
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead_queues.append(queue)
                logger.warning("Dropping slow SSE subscriber and closing connection")

        for queue in dead_queues:
            self._subscribers.discard(queue)
            self._close_queue(queue)

    async def shutdown(self) -> None:
        """Signal all subscribers to disconnect."""
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                self._close_queue(queue)
        self._subscribers.clear()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


event_bus = EventBus()


async def publish_resource_event(
    resource_type: ResourceType,
    action: EventAction,
    resource_id: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    """Convenience function to publish a resource change event."""
    event = ResourceEvent(
        resource_type=resource_type,
        action=action,
        resource_id=resource_id,
        details=details,
    )
    await event_bus.publish(event)
