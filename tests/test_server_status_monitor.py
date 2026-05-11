#!/usr/bin/env python3
#
# tests/test_server_status_monitor.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
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


def _build_sequenced_session_factory(*sessions):
    session_iter = iter(sessions)

    def session_factory(*args, **kwargs):
        del args, kwargs
        return _FakeSessionContext(next(session_iter))

    return session_factory


class ServerStatusMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_server_status_returns_offline_on_timeout(self) -> None:
        server = SimpleNamespace(id=7, status="online")

        async def slow_probe(_server):
            await asyncio.sleep(0.05)
            return {}

        with patch(
            "app.services.server_status_monitor.caddy_service.test_connection",
            new=AsyncMock(side_effect=slow_probe),
        ):
            status = await server_status_monitor._probe_server_status(server, timeout_seconds=0.001)

        self.assertEqual(status, "offline")

    async def test_probe_server_statuses_once_commits_and_publishes_on_status_change(self) -> None:
        read_session = SimpleNamespace()
        write_session = SimpleNamespace(commit=AsyncMock(), execute=AsyncMock())
        session_factory = _build_sequenced_session_factory(read_session, write_session)
        server = SimpleNamespace(id=7, status="offline")

        with (
            patch("app.services.server_status_monitor.server_repository.list_all", new=AsyncMock(return_value=[server])) as list_all,
            patch("app.services.server_status_monitor.caddy_service.test_connection", new=AsyncMock(return_value={})) as test_connection,
            patch("app.services.server_status_monitor.publish_resource_event", new=AsyncMock()) as publish_resource_event,
        ):
            await server_status_monitor.probe_server_statuses_once(session_factory)

        list_all.assert_awaited_once_with(read_session, limit=server_status_monitor.SERVER_STATUS_QUERY_LIMIT)
        test_connection.assert_awaited_once_with(server)
        write_session.execute.assert_awaited_once()
        write_session.commit.assert_awaited_once()
        publish_resource_event.assert_awaited_once_with(
            "server",
            "updated",
            "7",
            details={"previous_status": "offline", "status": "online"},
        )

    async def test_probe_server_statuses_once_skips_commit_when_status_is_unchanged(self) -> None:
        read_session = SimpleNamespace()
        session_factory = _build_session_factory(read_session)
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

        list_all.assert_awaited_once_with(read_session, limit=server_status_monitor.SERVER_STATUS_QUERY_LIMIT)
        test_connection.assert_awaited_once_with(server)
        publish_resource_event.assert_not_awaited()

    async def test_probe_server_statuses_once_commits_and_publishes_on_online_to_offline(self) -> None:
        read_session = SimpleNamespace()
        write_session = SimpleNamespace(commit=AsyncMock(), execute=AsyncMock())
        session_factory = _build_sequenced_session_factory(read_session, write_session)
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

        list_all.assert_awaited_once_with(read_session, limit=server_status_monitor.SERVER_STATUS_QUERY_LIMIT)
        test_connection.assert_awaited_once_with(server)
        write_session.execute.assert_awaited_once()
        write_session.commit.assert_awaited_once()
        publish_resource_event.assert_awaited_once_with(
            "server",
            "updated",
            "7",
            details={"previous_status": "online", "status": "offline"},
        )

    async def test_probe_server_statuses_once_commits_once_for_mixed_batch(self) -> None:
        read_session = SimpleNamespace()
        write_session = SimpleNamespace(commit=AsyncMock(), execute=AsyncMock())
        session_factory = _build_sequenced_session_factory(read_session, write_session)
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

        list_all.assert_awaited_once_with(read_session, limit=server_status_monitor.SERVER_STATUS_QUERY_LIMIT)
        self.assertEqual(test_connection_mock.await_count, 3)
        self.assertEqual(write_session.execute.await_count, 1)
        write_session.commit.assert_awaited_once()
        publish_resource_event.assert_awaited_once_with(
            "server",
            "updated",
            "2",
            details={"previous_status": "offline", "status": "online"},
        )