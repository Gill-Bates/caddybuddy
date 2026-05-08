#!/usr/bin/env python3
#
# tests/test_ui_profile.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from app.routers.ui import profile


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


class UiProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_profile_rejects_empty_username_before_repository_call(self) -> None:
        request = _build_request("/profile")
        current_user = SimpleNamespace(id=1, username="alice")

        with (
            patch("app.routers.ui.profile.require_user", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.profile.validated_form",
                new=AsyncMock(return_value={"username": "   ", "email": "alice@example.com"}),
            ),
            patch("app.routers.ui.profile.user_repository.update_profile", new=AsyncMock()) as update_profile,
        ):
            response = await profile.update_profile(request, session=object())

        update_profile.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/profile")
        self.assertEqual(
            request.session["flashes"],
            [{
                "category": "danger",
                "message": "Username is required and must be at most 50 characters.",
            }],
        )

    async def test_update_profile_records_redirect_status_in_audit_log(self) -> None:
        request = _build_request("/profile")
        current_user = SimpleNamespace(id=1, username="alice")

        with (
            patch("app.routers.ui.profile.require_user", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.profile.validated_form",
                new=AsyncMock(return_value={"username": "alice", "email": "alice@example.com"}),
            ),
            patch("app.routers.ui.profile.user_repository.update_profile", new=AsyncMock()),
            patch("app.routers.ui.profile.audit_commit_and_flash", new=AsyncMock()) as audit_commit_and_flash,
        ):
            response = await profile.update_profile(request, session=object())

        self.assertEqual(response.status_code, 303)
        audit_commit_and_flash.assert_awaited_once()
        self.assertEqual(audit_commit_and_flash.await_args.kwargs["status_code"], 303)

    async def test_change_password_rejects_short_password_before_service_call(self) -> None:
        request = _build_request("/profile/password")
        current_user = SimpleNamespace(id=1, password_hash="hash")

        with (
            patch("app.routers.ui.profile.require_user", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.profile.validated_form",
                new=AsyncMock(
                    return_value={
                        "current_password": "CurrentPassword1!",
                        "new_password": "short1!A",
                        "confirm_password": "short1!A",
                    }
                ),
            ),
            patch("app.routers.ui.profile.auth_service.verify_password", new=AsyncMock()) as verify_password,
        ):
            response = await profile.change_password(request, session=object())

        verify_password.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/profile")
        self.assertEqual(
            request.session["flashes"],
            [{
                "category": "danger",
                "message": "Password must be at least 12 characters long.",
            }],
        )

    async def test_change_password_records_redirect_status_in_audit_log(self) -> None:
        request = _build_request("/profile/password")
        current_user = SimpleNamespace(id=1, password_hash="hash")

        with (
            patch("app.routers.ui.profile.require_user", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.profile.validated_form",
                new=AsyncMock(
                    return_value={
                        "current_password": "CurrentPassword1!",
                        "new_password": "LongerPassword1!",
                        "confirm_password": "LongerPassword1!",
                    }
                ),
            ),
            patch("app.routers.ui.profile.auth_service.verify_password", new=AsyncMock(return_value=True)),
            patch("app.routers.ui.profile.auth_service.update_password", new=AsyncMock()),
            patch("app.routers.ui.profile.audit_commit_and_flash", new=AsyncMock()) as audit_commit_and_flash,
        ):
            response = await profile.change_password(request, session=object())

        self.assertEqual(response.status_code, 303)
        audit_commit_and_flash.assert_awaited_once()
        self.assertEqual(audit_commit_and_flash.await_args.kwargs["status_code"], 303)


if __name__ == "__main__":
    unittest.main()