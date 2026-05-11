#!/usr/bin/env python3

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import IntegrityError

os.environ.setdefault("CADDYBUDDY_SECRET_KEY", "0123456789abcdef0123456789abcdef0123456789abcdef")
os.environ.setdefault("CADDYBUDDY_ALLOW_INSECURE_DEFAULTS", "true")

from app.services.auth import AuthorizationError, AuthService


class AuthServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_default_admin_returns_none_on_integrity_conflict(self) -> None:
        service = AuthService()
        session = SimpleNamespace(rollback=AsyncMock())

        with (
            patch("app.services.auth.get_settings", return_value=SimpleNamespace(allow_insecure_defaults=True)),
            patch("app.services.auth.user_repository.count", new=AsyncMock(return_value=0)),
            patch("app.services.auth.user_repository.create", new=AsyncMock(side_effect=IntegrityError("stmt", "params", Exception("duplicate")))),
            patch.object(service, "hash_password", new=AsyncMock(return_value="hashed")),
        ):
            created_user = await service.ensure_default_admin(
                session,
                username="admin",
                password="AdminPass123!",
                email="admin@example.com",
            )

        self.assertIsNone(created_user)
        session.rollback.assert_awaited_once()

    def test_require_admin_raises_authorization_error(self) -> None:
        actor = SimpleNamespace(role="user")

        with self.assertRaises(AuthorizationError):
            AuthService._require_admin(actor)

    def test_require_self_or_admin_raises_authorization_error(self) -> None:
        actor = SimpleNamespace(id=7, role="user")

        with self.assertRaises(AuthorizationError):
            AuthService._require_self_or_admin(actor, 8)


if __name__ == "__main__":
    unittest.main()