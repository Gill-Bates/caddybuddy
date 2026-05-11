#!/usr/bin/env python3

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from app.routers.ui import auth


def _build_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "query_string": b"",
            "session": {},
            "client": ("127.0.0.1", 12345),
        }
    )


class UiAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_rejects_excessive_username_length_before_authentication(self) -> None:
        request = _build_request("/login")
        username = "u" * 51

        with (
            patch(
                "app.routers.ui.auth.validated_form",
                new=AsyncMock(return_value={"username": username, "password": "Password123!", "next": "/"}),
            ),
            patch("app.routers.ui.auth.audit_commit_and_flash", new=AsyncMock()) as audit_commit_and_flash,
            patch("app.routers.ui.auth.auth_service.authenticate", new=AsyncMock()) as authenticate,
            patch("app.routers.ui.auth.initialize_user_session") as initialize_user_session,
        ):
            response = await auth.login_action(request, session=object())

        authenticate.assert_not_awaited()
        initialize_user_session.assert_not_called()
        audit_commit_and_flash.assert_awaited_once()
        self.assertEqual(audit_commit_and_flash.await_args.kwargs["status_code"], 400)
        self.assertEqual(
            audit_commit_and_flash.await_args.kwargs["details"],
            {"reason": "excessive_username_length"},
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")


if __name__ == "__main__":
    unittest.main()