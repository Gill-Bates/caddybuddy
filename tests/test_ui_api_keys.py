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
        self.assertEqual(response.headers["Vary"], "Cookie")

    async def test_create_api_key_rejects_empty_permissions_before_service_call(self) -> None:
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
                new=AsyncMock(return_value={"name": "demo"}),
            ),
            patch("app.routers.ui.api_keys.auth_service.create_api_key", new=AsyncMock()) as create_api_key,
        ):
            response = await api_keys.create_api_key(request, session=object())

        create_api_key.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/api-keys")
        self.assertEqual(
            request.session["flashes"],
            [{"category": "danger", "message": "API key must have at least one permission."}],
        )

    async def test_create_api_key_rejects_invalid_expiration_before_service_call(self) -> None:
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
                new=AsyncMock(return_value={"name": "demo", "perm_read": "on", "expires_days": "abc"}),
            ),
            patch("app.routers.ui.api_keys.auth_service.create_api_key", new=AsyncMock()) as create_api_key,
        ):
            response = await api_keys.create_api_key(request, session=object())

        create_api_key.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/api-keys")
        self.assertEqual(
            request.session["flashes"],
            [{"category": "danger", "message": "Expiration must be a non-negative number of days."}],
        )

    async def test_create_api_key_swallows_event_publish_failures(self) -> None:
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
        api_key_record = SimpleNamespace(id=7, name="demo")
        session = object()

        with (
            patch("app.routers.ui.api_keys.require_user", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.api_keys.validated_form",
                new=AsyncMock(return_value={"name": "demo", "perm_read": "on"}),
            ),
            patch(
                "app.routers.ui.api_keys.auth_service.create_api_key",
                new=AsyncMock(return_value=(api_key_record, "raw-secret")),
            ),
            patch("app.routers.ui.api_keys.audit_commit_and_flash", new=AsyncMock()),
            patch(
                "app.routers.ui.api_keys.publish_resource_event",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch(
                "app.routers.ui.api_keys.load_api_keys",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.routers.ui.api_keys.render_template",
                return_value=SimpleNamespace(headers={}, status_code=201),
            ),
            patch("app.routers.ui.api_keys.logger.exception"),
        ):
            response = await api_keys.create_api_key(request, session=session)

        self.assertEqual(response.status_code, 201)

    async def test_toggle_api_key_uses_atomic_authorized_toggle_and_redirect_audit_status(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api-keys/7/toggle",
                "headers": [],
                "query_string": b"",
                "session": {},
                "client": ("127.0.0.1", 12345),
            }
        )
        current_user = SimpleNamespace(id=1, role="user")
        session = object()

        with (
            patch("app.routers.ui.api_keys.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.api_keys.validated_form", new=AsyncMock(return_value={})),
            patch(
                "app.routers.ui.api_keys.api_key_repository.toggle_active_for_actor",
                new=AsyncMock(return_value=(7, False)),
            ) as toggle_active_for_actor,
            patch("app.routers.ui.api_keys.audit_commit_and_flash", new=AsyncMock()) as audit_commit,
            patch("app.routers.ui.api_keys.publish_resource_event", new=AsyncMock()),
        ):
            response = await api_keys.toggle_api_key(request, api_key_id=7, session=session)

        toggle_active_for_actor.assert_awaited_once_with(
            session,
            api_key_id=7,
            actor=current_user,
        )
        self.assertEqual(audit_commit.await_args.kwargs["status_code"], 303)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/api-keys")

    async def test_toggle_api_key_hides_unauthorized_as_not_found(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api-keys/7/toggle",
                "headers": [],
                "query_string": b"",
                "session": {},
                "client": ("127.0.0.1", 12345),
            }
        )
        current_user = SimpleNamespace(id=1, role="user")

        with (
            patch("app.routers.ui.api_keys.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.api_keys.validated_form", new=AsyncMock(return_value={})),
            patch(
                "app.routers.ui.api_keys.api_key_repository.toggle_active_for_actor",
                new=AsyncMock(return_value=None),
            ),
        ):
            response = await api_keys.toggle_api_key(request, api_key_id=7, session=object())

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/api-keys")
        self.assertEqual(
            request.session["flashes"],
            [{"category": "danger", "message": "API key not found."}],
        )


if __name__ == "__main__":
    unittest.main()