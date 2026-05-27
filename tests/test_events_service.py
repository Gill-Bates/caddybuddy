#!/usr/bin/env python3
#
# tests/test_events_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import unittest

from app.services.events import EventBus, ResourceEvent


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


class EventBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_discards_pending_events_and_closes_subscriber(self) -> None:
        bus = EventBus()
        events = bus.subscribe()

        await bus.publish(ResourceEvent(resource_type="site", action="created", resource_id="1"))
        await bus.shutdown()

        with self.assertRaises(StopAsyncIteration):
            await anext(events)

    async def test_slow_subscriber_is_dropped_when_queue_fills(self) -> None:
        bus = EventBus(queue_size=1)
        events = bus.subscribe()

        await bus.publish(ResourceEvent(resource_type="site", action="created", resource_id="1"))
        await bus.publish(ResourceEvent(resource_type="site", action="updated", resource_id="1"))

        self.assertEqual(bus.subscriber_count, 0)
        with self.assertRaises(StopAsyncIteration):
            await anext(events)

    async def test_request_shutdown_closes_subscriber_from_sync_exit_path(self) -> None:
        bus = EventBus()
        events = bus.subscribe()

        bus.request_shutdown()
        await asyncio.sleep(0)

        with self.assertRaises(StopAsyncIteration):
            await anext(events)


if __name__ == "__main__":
    unittest.main()