#!/usr/bin/env python3
#
# tests/test_ui_api_keys.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from app.routers.ui import api_keys


class UiApiKeysTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_api_key_rejects_overlong_name_before_service_call(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api-keys",
                "headers": [],
                "query_string": b"",
                "session": {},
                "client": ("127.0.0.1", 12345),
            }
        )
        current_user = SimpleNamespace(id=1, role="user")

        with (
            patch("app.routers.ui.api_keys.require_user", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.api_keys.validated_form",
                new=AsyncMock(return_value={"name": "x" * 121}),
            ),
            patch("app.routers.ui.api_keys.auth_service.create_api_key", new=AsyncMock()) as create_api_key,
        ):
            response = await api_keys.create_api_key(request, session=object())

        create_api_key.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/api-keys")
        self.assertEqual(
            request.session["flashes"],
            [{
                "category": "danger",
                "message": "API key name must be between 1 and 120 characters.",
            }],
        )

    async def test_render_api_keys_page_marks_pending_key_response_uncacheable(self) -> None:
        request = SimpleNamespace(session={})
        current_user = SimpleNamespace(role="user")
        response = SimpleNamespace(headers={})

        with (
            patch("app.routers.ui.api_keys.load_api_keys", new=AsyncMock(return_value=[])),
            patch("app.routers.ui.api_keys.render_template", return_value=response),
        ):
            returned = await api_keys._render_api_keys_page(
                request,
                session=object(),
                current_user=current_user,
                pending_api_key="raw-secret",
                status_code=201,
            )

        self.assertIs(returned, response)
        self.assertEqual(
            response.headers["Cache-Control"],
            "private, no-store, no-cache, must-revalidate, max-age=0",
        )
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(response.headers["Expires"], "0")


if __name__ == "__main__":
    unittest.main()