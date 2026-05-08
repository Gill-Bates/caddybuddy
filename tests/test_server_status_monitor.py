#!/usr/bin/env python3
#
# tests/test_server_status_monitor.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import server_status_monitor


class _FakeSessionContext:
    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _build_session_factory(session):
    def session_factory(*args, **kwargs):
        del args, kwargs
        return _FakeSessionContext(session)

    return session_factory


class ServerStatusMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_server_statuses_once_commits_and_publishes_on_status_change(self) -> None:
        session = SimpleNamespace(commit=AsyncMock())
        session_factory = _build_session_factory(session)
        server = SimpleNamespace(id=7, status="offline")

        with (
            patch("app.services.server_status_monitor.server_repository.list_all", new=AsyncMock(return_value=[server])) as list_all,
            patch("app.services.server_status_monitor.caddy_service.test_connection", new=AsyncMock(return_value={})) as test_connection,
            patch("app.services.server_status_monitor.publish_resource_event", new=AsyncMock()) as publish_resource_event,
        ):
            await server_status_monitor.probe_server_statuses_once(session_factory)

        list_all.assert_awaited_once_with(session)
        test_connection.assert_awaited_once_with(server)
        self.assertEqual(server.status, "online")
        session.commit.assert_awaited_once()
        publish_resource_event.assert_awaited_once_with(
            "server",
            "updated",
            "7",
            details={"previous_status": "offline", "status": "online"},
        )

    async def test_probe_server_statuses_once_skips_commit_when_status_is_unchanged(self) -> None:
        session = SimpleNamespace(commit=AsyncMock())
        session_factory = _build_session_factory(session)
        server = SimpleNamespace(id=7, status="offline")

        with (
            patch(
                "app.services.server_status_monitor.server_repository.list_all",
                new=AsyncMock(return_value=[server]),
            ) as list_all,
            patch(
                "app.services.server_status_monitor.caddy_service.test_connection",
                new=AsyncMock(side_effect=server_status_monitor.CaddyServiceError("down")),
            ) as test_connection,
            patch("app.services.server_status_monitor.publish_resource_event", new=AsyncMock()) as publish_resource_event,
        ):
            await server_status_monitor.probe_server_statuses_once(session_factory)

        list_all.assert_awaited_once_with(session)
        test_connection.assert_awaited_once_with(server)
        session.commit.assert_not_awaited()
        publish_resource_event.assert_not_awaited()

    async def test_probe_server_statuses_once_commits_and_publishes_on_online_to_offline(self) -> None:
        session = SimpleNamespace(commit=AsyncMock())
        session_factory = _build_session_factory(session)
        server = SimpleNamespace(id=7, status="online")

        with (
            patch(
                "app.services.server_status_monitor.server_repository.list_all",
                new=AsyncMock(return_value=[server]),
            ) as list_all,
            patch(
                "app.services.server_status_monitor.caddy_service.test_connection",
                new=AsyncMock(side_effect=server_status_monitor.CaddyServiceError("down")),
            ) as test_connection,
            patch("app.services.server_status_monitor.publish_resource_event", new=AsyncMock()) as publish_resource_event,
        ):
            await server_status_monitor.probe_server_statuses_once(session_factory)

        list_all.assert_awaited_once_with(session)
        test_connection.assert_awaited_once_with(server)
        self.assertEqual(server.status, "offline")
        session.commit.assert_awaited_once()
        publish_resource_event.assert_awaited_once_with(
            "server",
            "updated",
            "7",
            details={"previous_status": "online", "status": "offline"},
        )

    async def test_probe_server_statuses_once_commits_once_for_mixed_batch(self) -> None:
        session = SimpleNamespace(commit=AsyncMock())
        session_factory = _build_session_factory(session)
        unchanged_online = SimpleNamespace(id=1, status="online")
        changed_server = SimpleNamespace(id=2, status="offline")
        unchanged_offline = SimpleNamespace(id=3, status="offline")

        async def test_connection(server):
            if server is unchanged_offline:
                raise server_status_monitor.CaddyServiceError("down")
            return {}

        with (
            patch(
                "app.services.server_status_monitor.server_repository.list_all",
                new=AsyncMock(return_value=[unchanged_online, changed_server, unchanged_offline]),
            ) as list_all,
            patch(
                "app.services.server_status_monitor.caddy_service.test_connection",
                new=AsyncMock(side_effect=test_connection),
            ) as test_connection_mock,
            patch("app.services.server_status_monitor.publish_resource_event", new=AsyncMock()) as publish_resource_event,
        ):
            await server_status_monitor.probe_server_statuses_once(session_factory)

        list_all.assert_awaited_once_with(session)
        self.assertEqual(test_connection_mock.await_count, 3)
        self.assertEqual(unchanged_online.status, "online")
        self.assertEqual(changed_server.status, "online")
        self.assertEqual(unchanged_offline.status, "offline")
        session.commit.assert_awaited_once()
        publish_resource_event.assert_awaited_once_with(
            "server",
            "updated",
            "2",
            details={"previous_status": "offline", "status": "online"},
        )