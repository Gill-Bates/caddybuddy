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

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, JsonValue] | list[JsonValue]
type EventDetails = dict[str, JsonValue]

ResourceType = Literal["site", "caddyfile", "ssllabs_scan"]
EventAction = Literal[
    "created",
    "updated",
    "deleted",
    "deployed",
    "onboarded",
    "sync_failed",
    "sync_skipped",
    "validation_failed",
    "scan_started",
    "scan_updated",
    "scan_completed",
    "scan_failed",
]

_MAX_SUBSCRIBER_QUEUE_SIZE = 64
_MAX_EVENT_PAYLOAD_BYTES = 8 * 1024


class SubscriberLimitReachedError(RuntimeError):
    """Raised when the SSE subscriber limit has been reached."""


def _validate_event_payload_size(payload: str) -> None:
    if len(payload.encode("utf-8")) > _MAX_EVENT_PAYLOAD_BYTES:
        raise ValueError("event payload exceeds size limit")


@dataclass(frozen=True, slots=True)
class ResourceEvent:
    """Immutable event representing a resource change."""

    resource_type: ResourceType
    action: EventAction
    resource_id: str | None = None
    details: EventDetails | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    _payload: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            payload = json.dumps(
                {
                    "type": self.resource_type,
                    "action": self.action,
                    "id": self.resource_id,
                    "details": self.details or {},
                    "ts": self.timestamp,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("event details must be JSON-serializable") from exc
        _validate_event_payload_size(payload)
        object.__setattr__(self, "_payload", payload)

    def to_json(self) -> str:
        return self._payload


class EventBus:
    """
    Process-local event bus for SSE subscribers.

    All methods must run on the application's main asyncio event loop. Delivery
    is in-memory and only reliable with a single application worker. Multi-
    worker deployments need an external pub-sub backend.

    Subscribers are tracked via one asyncio.Queue per client. Slow subscribers
    are actively terminated so their blocked generator tasks can exit cleanly.
    """

    def __init__(self, max_subscribers: int = 100, queue_size: int = _MAX_SUBSCRIBER_QUEUE_SIZE) -> None:
        self._subscribers: set[asyncio.Queue[ResourceEvent | None]] = set()
        self._max_subscribers = max_subscribers
        self._queue_size = queue_size
        self._loop: asyncio.AbstractEventLoop | None = None

    def _capture_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._loop is None or self._loop.is_closed():
            self._loop = loop

    @staticmethod
    def _close_queue(queue: asyncio.Queue[ResourceEvent | None]) -> None:
        """Drain a queue and enqueue the shutdown sentinel to unblock waiters."""
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        queue.put_nowait(None)

    def _close_all_subscribers(self) -> None:
        for queue in tuple(self._subscribers):
            self._close_queue(queue)
        self._subscribers.clear()

    def subscribe(self) -> AsyncIterator[ResourceEvent]:
        """
        Yield events as they are published.

        The generator exits when the client disconnects or the bus is shut down.
        """
        self._capture_loop()
        if len(self._subscribers) >= self._max_subscribers:
            logger.warning("Max SSE subscribers reached, rejecting new connection")
            raise SubscriberLimitReachedError("Max SSE subscribers reached")

        queue: asyncio.Queue[ResourceEvent | None] = asyncio.Queue(maxsize=self._queue_size)
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
        """Broadcast an event without blocking on slow subscribers."""
        self._capture_loop()
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
        self._close_all_subscribers()

    def request_shutdown(self) -> None:
        """Schedule subscriber shutdown from sync exit handlers."""
        loop = self._loop
        if loop is None or loop.is_closed():
            self._close_all_subscribers()
            return
        loop.call_soon_threadsafe(self._close_all_subscribers)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Process-local SSE bus. Run the app with a single worker or replace this with
# an external pub-sub backend for multi-worker deployments.
event_bus = EventBus()


async def publish_resource_event(
    resource_type: ResourceType,
    action: EventAction,
    resource_id: str | None = None,
    details: EventDetails | None = None,
) -> None:
    """Convenience function to publish a resource change event."""
    event = ResourceEvent(
        resource_type=resource_type,
        action=action,
        resource_id=resource_id,
        details=details,
    )
    await event_bus.publish(event)
