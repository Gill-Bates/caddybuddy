#!/usr/bin/env python3
#
# tests/test_ui_common.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import FormData

from app.routers.ui import _common


class UiCommonTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_form_returns_formdata(self) -> None:
        form = FormData({"csrf_token": "token", "name": "value"})
        request = SimpleNamespace(form=AsyncMock(return_value=form))

        with patch("app.routers.ui._common.validate_csrf_token") as validate_csrf_token:
            returned = await _common.validated_form(request)

        self.assertIs(returned, form)
        validate_csrf_token.assert_called_once_with(request, "token")

    async def test_validated_form_propagates_http_403_for_invalid_csrf(self) -> None:
        form = FormData({"csrf_token": "bad-token"})
        request = SimpleNamespace(form=AsyncMock(return_value=form))

        with patch(
            "app.routers.ui._common.validate_csrf_token",
            side_effect=HTTPException(status_code=403, detail="Invalid CSRF token."),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await _common.validated_form(request)

        self.assertEqual(exc_info.exception.status_code, 403)

    async def test_audit_commit_and_flash_rolls_back_on_commit_failure(self) -> None:
        session = SimpleNamespace(
            commit=AsyncMock(side_effect=SQLAlchemyError("commit failed")),
            rollback=AsyncMock(),
            is_active=True,
        )
        request = object()

        with (
            patch("app.routers.ui._common.audit_service.log_action", new=AsyncMock()) as log_action,
            patch("app.routers.ui._common.push_flash") as push_flash,
        ):
            with self.assertRaisesRegex(SQLAlchemyError, "commit failed"):
                await _common.audit_commit_and_flash(
                    session,
                    request,
                    action="user.login",
                    resource_type="user",
                    flashes=(("success", "Signed in."),),
                )

        log_action.assert_awaited_once()
        session.rollback.assert_awaited_once()
        push_flash.assert_not_called()

    async def test_audit_commit_and_flash_skips_rollback_for_inactive_session(self) -> None:
        session = SimpleNamespace(
            commit=AsyncMock(side_effect=SQLAlchemyError("commit failed")),
            rollback=AsyncMock(),
            is_active=False,
        )
        request = object()

        with patch("app.routers.ui._common.audit_service.log_action", new=AsyncMock()):
            with self.assertRaisesRegex(SQLAlchemyError, "commit failed"):
                await _common.audit_commit_and_flash(
                    session,
                    request,
                    action="user.login",
                    resource_type="user",
                )

        session.rollback.assert_not_awaited()

    async def test_load_dashboard_context_uses_count_star_queries(self) -> None:
        row = SimpleNamespace(
            server_count=1,
            config_count=2,
            api_key_count=3,
            audit_count=4,
        )
        execute_result = MagicMock()
        execute_result.one.return_value = row
        session = SimpleNamespace(execute=AsyncMock(return_value=execute_result))

        with (
            patch("app.routers.ui._common.server_repository.list_all", new=AsyncMock(return_value=["server"])) as list_all,
            patch("app.routers.ui._common.audit_log_repository.list_recent", new=AsyncMock(return_value=["log"])) as list_recent,
        ):
            context = await _common.load_dashboard_context(session)

        statement = session.execute.await_args.args[0]
        compiled_sql = str(statement.compile(compile_kwargs={"literal_binds": True}))

        self.assertIn("count(*)", compiled_sql.lower())
        self.assertEqual(context["server_count"], 1)
        self.assertEqual(context["config_count"], 2)
        self.assertEqual(context["api_key_count"], 3)
        self.assertEqual(context["audit_count"], 4)
        self.assertEqual(context["servers"], ["server"])
        self.assertEqual(context["recent_logs"], ["log"])
        list_all.assert_awaited_once_with(session, limit=5)
        list_recent.assert_awaited_once_with(session, limit=8)

    def test_safe_next_accepts_none(self) -> None:
        self.assertEqual(_common.safe_next(None), "/")

    def test_safe_next_rejects_control_characters(self) -> None:
        self.assertEqual(_common.safe_next("/foo\nbar"), "/")
        self.assertEqual(_common.safe_next("/foo\tbar"), "/")

    def test_safe_next_rejects_backslashes(self) -> None:
        self.assertEqual(_common.safe_next("/%5Cevil.example"), "/")
        self.assertEqual(_common.safe_next("/\\evil.example"), "/")
        self.assertEqual(_common.safe_next("/foo\\bar"), "/")

    def test_safe_next_rejects_encoded_control_characters(self) -> None:
        self.assertEqual(_common.safe_next("/%0aevil"), "/")


if __name__ == "__main__":
    unittest.main()