#!/usr/bin/env python3
#
# tests/test_ui_deployments.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from starlette.requests import Request
from starlette.staticfiles import StaticFiles

from app.routers.ui import deployments as deployments_ui
from app.routers.ui import router as ui_router


def _build_request(path: str) -> Request:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory="/opt/caddybuddy/app/static"), name="static")
    app.include_router(ui_router)
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "session": {},
            "client": ("127.0.0.1", 12345),
            "app": app,
        }
    )


class UiDeploymentsTests(unittest.IsolatedAsyncioTestCase):
    async def test_deployments_page_renders_initiator_column_and_scrollable_table(self) -> None:
        request = _build_request("/deployments")
        session = object()
        current_user = SimpleNamespace(id=1, role="admin", username="alice", is_admin=True)
        deployment = SimpleNamespace(
            id=9,
            site=SimpleNamespace(id=4, domain="secure.example.com"),
            server=SimpleNamespace(id=7, name="edge-1"),
            status=SimpleNamespace(value="deployed"),
            deployed_at=datetime(2026, 5, 11, 12, 30, tzinfo=UTC),
            deployed_by="alice",
        )

        with (
            patch("app.routers.ui.deployments.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.deployments.deployment_repository.list_all", new=AsyncMock(return_value=[deployment])),
            patch("app.routers.ui.deployments.deployment_repository.get_by_id", new=AsyncMock(return_value=None)),
        ):
            response = await deployments_ui.deployments_page(request, session=session)

        self.assertEqual(response.status_code, 200)
        body = response.body.decode("utf-8")
        self.assertIn("Initiated By", body)
        self.assertIn("deployments-table-card", body)
        self.assertIn("deployments-table-scroll", body)
        self.assertIn("alice", body)


if __name__ == "__main__":
    unittest.main()