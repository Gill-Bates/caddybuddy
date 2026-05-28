#!/usr/bin/env python3
#
# tests/test_events_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
from contextlib import suppress
import unittest

from app.services.events import EventBus, ResourceEvent, try_publish_resource_event


class ResourceEventTests(unittest.TestCase):
    def test_to_json_is_compact_and_preserves_unicode(self) -> None:
        event = ResourceEvent(
            resource_type="site",
            action="updated",
            resource_id="1",
            details={"domain": "münchen.example"},
            timestamp="2026-05-27T12:00:00+00:00",
        )

        self.assertEqual(
            event.to_json(),
            '{"type":"site","action":"updated","id":"1","details":{"domain":"münchen.example"},"ts":"2026-05-27T12:00:00+00:00"}',
        )

    def test_rejects_non_json_serializable_details(self) -> None:
        with self.assertRaisesRegex(ValueError, "JSON-serializable"):
            ResourceEvent(
                resource_type="site",
                action="updated",
                details={"broken": {1, 2, 3}},
            )

    def test_rejects_oversized_payload(self) -> None:
        oversized_details = {"payload": "x" * (8 * 1024)}

        with self.assertRaisesRegex(ValueError, "size limit"):
            ResourceEvent(
                resource_type="caddyfile",
                action="validation_failed",
                details=oversized_details,
            )

    def test_details_are_copied_on_construction(self) -> None:
        details = {"domain": "example.com"}

        event = ResourceEvent(
            resource_type="site",
            action="updated",
            details=details,
        )

        details["domain"] = "mutated.example.com"
        self.assertEqual(event.details["domain"], "example.com")
        self.assertIn('"domain":"example.com"', event.to_json())


class EventBusConfigTests(unittest.TestCase):
    def test_rejects_non_positive_limits(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_subscribers"):
            EventBus(max_subscribers=0)

        with self.assertRaisesRegex(ValueError, "queue_size"):
            EventBus(queue_size=0)

    def test_subscribe_requires_running_loop(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "application event loop"):
            EventBus().subscribe()


class EventBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_discards_pending_events_and_closes_subscriber(self) -> None:
        bus = EventBus()
        events = bus.subscribe()

        first_event = asyncio.create_task(anext(events))
        await asyncio.sleep(0)
        await bus.publish(ResourceEvent(resource_type="site", action="created", resource_id="1"))
        received = await first_event
        self.assertEqual(received.action, "created")

        await bus.publish(ResourceEvent(resource_type="site", action="updated", resource_id="1"))
        await bus.shutdown()

        with self.assertRaises(StopAsyncIteration):
            await anext(events)

    async def test_slow_subscriber_is_dropped_when_queue_fills(self) -> None:
        bus = EventBus(queue_size=1)
        events = bus.subscribe()

        first_event = asyncio.create_task(anext(events))
        await asyncio.sleep(0)
        await bus.publish(ResourceEvent(resource_type="site", action="created", resource_id="1"))
        await first_event
        await bus.publish(ResourceEvent(resource_type="site", action="updated", resource_id="1"))
        await bus.publish(ResourceEvent(resource_type="site", action="deleted", resource_id="1"))

        self.assertEqual(bus.subscriber_count, 0)
        with self.assertRaises(StopAsyncIteration):
            await anext(events)

    async def test_request_shutdown_closes_subscriber_from_sync_exit_path(self) -> None:
        bus = EventBus()
        events = bus.subscribe()

        wait_task = asyncio.create_task(anext(events))
        await asyncio.sleep(0)
        bus.request_shutdown()
        await asyncio.sleep(0)
        with suppress(StopAsyncIteration):
            await wait_task

        with self.assertRaises(StopAsyncIteration):
            await anext(events)

    async def test_subscribe_defers_registration_until_iteration(self) -> None:
        bus = EventBus()
        events = bus.subscribe()

        self.assertEqual(bus.subscriber_count, 0)

        wait_task = asyncio.create_task(anext(events))
        await asyncio.sleep(0)

        self.assertEqual(bus.subscriber_count, 1)

        bus.request_shutdown()
        await asyncio.sleep(0)
        with suppress(StopAsyncIteration):
            await wait_task


class EventPublishingTests(unittest.IsolatedAsyncioTestCase):
    async def test_try_publish_resource_event_swallows_payload_errors(self) -> None:
        await try_publish_resource_event(
            "site",
            "updated",
            "1",
            {"broken": {1, 2, 3}},
        )


if __name__ == "__main__":
    unittest.main()