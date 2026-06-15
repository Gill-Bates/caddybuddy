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
from typing import Literal, Self


logger = logging.getLogger(__name__)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, JsonValue] | list[JsonValue]
type EventDetails = dict[str, JsonValue]

ResourceType = Literal["site", "caddyfile", "ssllabs_scan", "certificate"]
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
    "renewing",
    "renewed",
    "renewal_failed",
]

_MAX_SUBSCRIBER_QUEUE_SIZE = 64
_MAX_EVENT_PAYLOAD_BYTES = 8 * 1024


class SubscriberLimitReachedError(RuntimeError):
    """Raised when the SSE subscriber limit has been reached."""


def _validate_event_payload_size(payload: str) -> None:
    if len(payload.encode("utf-8")) > _MAX_EVENT_PAYLOAD_BYTES:
        raise ValueError("event payload exceeds size limit")


def _close_queue(queue: asyncio.Queue["ResourceEvent | None"]) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    queue.put_nowait(None)


@dataclass(frozen=True, slots=True)
class ResourceEvent:
    """Immutable event representing a resource change."""

    resource_type: ResourceType
    action: EventAction
    resource_id: str | None = None
    details: EventDetails = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    _payload: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            # Round-trip through JSON so ``details`` becomes a deep snapshot that
            # always matches the serialized payload; nested objects shared with
            # the caller can no longer mutate the stored event.
            normalized_details = json.loads(
                json.dumps(self.details, ensure_ascii=False, allow_nan=False)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("event details must be JSON-serializable") from exc
        if not isinstance(normalized_details, dict):
            raise ValueError("event details must be a JSON object")
        payload = json.dumps(
            {
                "type": self.resource_type,
                "action": self.action,
                "id": self.resource_id,
                "details": normalized_details,
                "ts": self.timestamp,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        _validate_event_payload_size(payload)
        object.__setattr__(self, "details", normalized_details)
        object.__setattr__(self, "_payload", payload)

    def to_json(self) -> str:
        return self._payload


class _SubscriberStream(AsyncIterator[ResourceEvent]):
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._queue: asyncio.Queue[ResourceEvent | None] = asyncio.Queue(maxsize=bus._queue_size)
        self._active = False
        self._closed = False

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> ResourceEvent:
        if self._closed:
            raise StopAsyncIteration

        if not self._active:
            self._bus._activate_subscriber(self)
            self._active = True

        try:
            event = await self._queue.get()
        except asyncio.CancelledError:
            self.close_from_bus()
            raise
        if event is None:
            self._closed = True
            self._active = False
            self._bus._release_subscriber(self)
            raise StopAsyncIteration
        return event

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._active = False
        self._bus._release_subscriber(self)

    def close_from_bus(self) -> None:
        if self._closed:
            return
        self._closed = True
        was_active = self._active
        self._active = False
        self._bus._release_subscriber(self)
        if was_active:
            _close_queue(self._queue)

    def deliver(self, event: ResourceEvent) -> None:
        self._queue.put_nowait(event)

    def __del__(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._active = False
        self._bus._release_subscriber(self)


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
        if max_subscribers < 1:
            raise ValueError("max_subscribers must be >= 1")
        if queue_size < 1:
            raise ValueError("queue_size must be >= 1")

        self._subscribers: set[_SubscriberStream] = set()
        self._pending_subscribers: set[_SubscriberStream] = set()
        self._max_subscribers = max_subscribers
        self._queue_size = queue_size
        self._loop: asyncio.AbstractEventLoop | None = None

    def _capture_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("EventBus methods must run inside the application event loop") from exc

        if self._loop is None or self._loop.is_closed():
            self._loop = loop
            return

        if self._loop is not loop:
            raise RuntimeError("EventBus used from a different event loop")

    def _activate_subscriber(self, subscriber: _SubscriberStream) -> None:
        self._pending_subscribers.discard(subscriber)
        self._subscribers.add(subscriber)

    def _release_subscriber(self, subscriber: _SubscriberStream) -> None:
        self._pending_subscribers.discard(subscriber)
        self._subscribers.discard(subscriber)

    def _close_all_subscribers(self) -> None:
        for subscriber in tuple(self._subscribers | self._pending_subscribers):
            subscriber.close_from_bus()
        self._subscribers.clear()
        self._pending_subscribers.clear()

    def subscribe(self) -> AsyncIterator[ResourceEvent]:
        """
        Yield events as they are published.

        The generator exits when the client disconnects or the bus is shut down.
        """
        self._capture_loop()
        total_subscribers = len(self._subscribers) + len(self._pending_subscribers)
        if total_subscribers >= self._max_subscribers:
            logger.warning("Max SSE subscribers reached, rejecting new connection")
            raise SubscriberLimitReachedError("Max SSE subscribers reached")
        subscriber = _SubscriberStream(self)
        self._pending_subscribers.add(subscriber)
        return subscriber

    async def publish(self, event: ResourceEvent) -> None:
        """Broadcast an event without blocking on slow subscribers."""
        self._capture_loop()
        dead_subscribers: list[_SubscriberStream] = []
        for subscriber in tuple(self._subscribers):
            try:
                subscriber.deliver(event)
            except asyncio.QueueFull:
                dead_subscribers.append(subscriber)
                logger.warning("Dropping slow SSE subscriber and closing connection")

        for subscriber in dead_subscribers:
            subscriber.close_from_bus()

    async def shutdown(self) -> None:
        """Signal all subscribers to disconnect."""
        self._capture_loop()
        self._close_all_subscribers()

    def request_shutdown(self) -> None:
        """Schedule subscriber shutdown from sync exit handlers."""
        loop = self._loop
        if loop is None or loop.is_closed():
            if self._subscribers:
                logger.warning("Cannot close SSE subscribers safely without a running event loop")
            for subscriber in tuple(self._pending_subscribers):
                subscriber.close_from_bus()
            self._subscribers.clear()
            self._pending_subscribers.clear()
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
        details=dict(details or {}),
    )
    await event_bus.publish(event)


async def try_publish_resource_event(
    resource_type: ResourceType,
    action: EventAction,
    resource_id: str | None = None,
    details: EventDetails | None = None,
) -> None:
    try:
        await publish_resource_event(
            resource_type=resource_type,
            action=action,
            resource_id=resource_id,
            details=details,
        )
    except Exception:
        logger.exception(
            "Failed to publish resource event",
            extra={
                "resource_type": resource_type,
                "action": action,
                "resource_id": resource_id,
            },
        )
