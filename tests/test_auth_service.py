#!/usr/bin/env python3
#
# tests/test_auth_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import hmac
import os
import unittest
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr

from app.config.settings import get_settings


_ENV_OVERRIDES = {
    "CB_SECRET_KEY": "unit-test-secret-key",
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key",
    "CB_ADMIN_PASSWORD": "UnitTestPassword-123A",
    "CADDYBUDDY_ADMIN_PASSWORD": "UnitTestPassword-123A",
}
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}

for key, value in _ENV_OVERRIDES.items():
    os.environ[key] = value

get_settings.cache_clear()

import app.services.auth as auth_module
from app.services.auth import AuthService, WeakPasswordError


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()
    auth_module._password_pepper_bytes.cache_clear()


class AuthServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        for key, value in _ENV_OVERRIDES.items():
            os.environ[key] = value
        get_settings.cache_clear()
        auth_module._password_pepper_bytes.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        auth_module._password_pepper_bytes.cache_clear()

    async def test_verify_password_returns_false_for_invalid_bcrypt_hash(self) -> None:
        verified = await AuthService.verify_password("Password123!", "not-a-bcrypt-hash")

        self.assertFalse(verified)

    async def test_authenticate_rejects_oversized_password_without_error(self) -> None:
        session = SimpleNamespace()
        oversized = "x" * 4097

        with patch.object(auth_module.user_repository, "get_by_username", new=AsyncMock()) as get_by_username:
            user = await auth_module.auth_service.authenticate(session, "admin", oversized)

        self.assertIsNone(user)
        get_by_username.assert_not_awaited()

    async def test_hash_password_enforces_strength_policy(self) -> None:
        with self.assertRaises(WeakPasswordError):
            await AuthService.hash_password("weakpassword12")

    async def test_hash_password_accepts_eight_character_password_when_policy_is_met(self) -> None:
        password_hash = await AuthService.hash_password("Abcdef1!")

        self.assertIsInstance(password_hash, str)
        self.assertTrue(password_hash)

    async def test_authenticate_treats_invalid_stored_hash_as_failed_login(self) -> None:
        session = SimpleNamespace()
        stored_user = SimpleNamespace(is_active=True, password_hash="broken-hash")

        with (
            patch.object(auth_module.user_repository, "get_by_username", new=AsyncMock(return_value=stored_user)),
            patch.object(auth_module.user_repository, "update_last_login", new=AsyncMock()) as update_last_login,
        ):
            user = await auth_module.auth_service.authenticate(session, "admin", "Password123!")

        self.assertIsNone(user)
        update_last_login.assert_not_awaited()

    async def test_authenticate_rejects_inactive_user(self) -> None:
        session = SimpleNamespace()
        stored_user = SimpleNamespace(is_active=False, password_hash="unused")

        with (
            patch.object(auth_module.user_repository, "get_by_username", new=AsyncMock(return_value=stored_user)),
            patch.object(AuthService, "verify_password", new=AsyncMock()) as verify_password,
        ):
            user = await auth_module.auth_service.authenticate(session, "admin", "Password123!")

        self.assertIsNone(user)
        verify_password.assert_awaited_once_with("Password123!", auth_module._DUMMY_BCRYPT_HASH)

    async def test_authenticate_updates_last_login_for_valid_credentials(self) -> None:
        session = SimpleNamespace()
        password = "Password123!"
        password_hash = await AuthService.hash_password(password)
        stored_user = SimpleNamespace(is_active=True, password_hash=password_hash)

        with (
            patch.object(auth_module.user_repository, "get_by_username", new=AsyncMock(return_value=stored_user)),
            patch.object(auth_module.user_repository, "update_last_login", new=AsyncMock()) as update_last_login,
        ):
            user = await auth_module.auth_service.authenticate(session, "admin", password)

        self.assertIs(user, stored_user)
        update_last_login.assert_awaited_once()
        called_when = update_last_login.await_args.args[2]
        self.assertIsInstance(called_when, datetime)
        self.assertEqual(called_when.tzinfo, UTC)

    async def test_hmac_digest_uses_password_pepper_when_configured(self) -> None:
        with patch.object(
            auth_module,
            "get_settings",
            return_value=SimpleNamespace(
                password_pepper=SecretStr("pepper-value"),
                secret_key=SecretStr("secret-value"),
            ),
        ):
            auth_module._password_pepper_bytes.cache_clear()
            digest = AuthService._hmac_digest("password", "Password123!")
            auth_module._password_pepper_bytes.cache_clear()

        expected = hmac.digest(
            b"pepper-value",
            b"password\0Password123!",
            sha256,
        )
        self.assertEqual(digest, expected)

    async def test_ensure_default_admin_accepts_explicit_weak_bootstrap_password(self) -> None:
        session = SimpleNamespace()
        created_user = SimpleNamespace(id=1)

        with (
            patch.object(auth_module.user_repository, "exists_any", new=AsyncMock(return_value=False)),
            patch.object(auth_module.AuthService, "_hash_password_unchecked", new=AsyncMock(return_value="hashed-password")) as hash_password,
            patch.object(auth_module.user_repository, "create", new=AsyncMock(return_value=created_user)) as create_user,
        ):
            user = await auth_module.auth_service.ensure_default_admin(
                session,
                username="admin",
                password="admin",
                email="admin@example.com",
            )

        self.assertIs(user, created_user)
        hash_password.assert_awaited_once_with("admin")
        create_user.assert_awaited_once_with(
            session,
            username="admin",
            email="admin@example.com",
            password_hash="hashed-password",
            role="admin",
        )


if __name__ == "__main__":
    unittest.main()