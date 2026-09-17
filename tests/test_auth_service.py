#!/usr/bin/env python3
#
# tests/test_auth_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import hmac
import unittest
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import bcrypt
from pydantic import SecretStr

from app.config.settings import get_settings
from tests.env_overrides import ModuleEnv

_ENV = ModuleEnv()

import app.services.auth as auth_module
from app.services.auth import AuthService, WeakPasswordError


def tearDownModule() -> None:
    _ENV.restore()
    auth_module._password_pepper_bytes.cache_clear()


class AuthServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _ENV.apply()
        auth_module._password_pepper_bytes.cache_clear()
        auth_module._otp_fernet.cache_clear()
        auth_module._recovery_code_key.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()
        auth_module._password_pepper_bytes.cache_clear()
        auth_module._otp_fernet.cache_clear()
        auth_module._recovery_code_key.cache_clear()

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

    async def test_hash_password_round_trips_when_hmac_digest_contains_nul_byte(self) -> None:
        password = "Password123!"
        self.assertIn(b"\x00", AuthService._hmac_digest("password", password))

        password_hash = await AuthService.hash_password(password)

        self.assertTrue(await AuthService.verify_password(password, password_hash))

    async def test_verify_password_accepts_legacy_nul_truncated_hash(self) -> None:
        password = "Password123!"
        legacy_input = AuthService._hmac_digest("password", password).split(b"\x00", 1)[0]
        password_hash = bcrypt.hashpw(legacy_input, bcrypt.gensalt()).decode("utf-8")

        self.assertTrue(await AuthService.verify_password(password, password_hash))

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

    async def test_authenticate_defers_last_login_until_second_factor_succeeds(self) -> None:
        session = SimpleNamespace()
        password = "Password123!"
        password_hash = await AuthService.hash_password(password)
        stored_user = SimpleNamespace(is_active=True, password_hash=password_hash)

        with (
            patch.object(auth_module.user_repository, "get_by_username", new=AsyncMock(return_value=stored_user)),
            patch.object(auth_module.user_repository, "update_last_login", new=AsyncMock()) as update_last_login,
        ):
            user = await auth_module.auth_service.authenticate(
                session,
                "admin",
                password,
                update_last_login=False,
            )

        self.assertIs(user, stored_user)
        update_last_login.assert_not_awaited()

    async def test_otp_factor_consumes_totp_counter_to_prevent_replay(self) -> None:
        secret = auth_module.generate_totp_secret()
        encrypted_secret = AuthService._encrypt_otp_secret(secret)
        counter = 1_234_567
        user = SimpleNamespace(
            otp_enabled=True,
            otp_secret=encrypted_secret,
            otp_recovery_codes=None,
        )

        with (
            patch.object(auth_module, "verify_totp", return_value=counter),
            patch.object(auth_module.user_repository, "consume_otp_counter", new=AsyncMock(return_value=True)) as consume,
        ):
            method = await auth_module.auth_service.verify_otp_factor(SimpleNamespace(), user, "123456")

        self.assertEqual(method, "totp")
        consume.assert_awaited_once_with(ANY, user, counter)

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

    async def test_ensure_default_admin_enforces_password_strength_and_uses_savepoint(self) -> None:
        class _Savepoint:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False

        session = SimpleNamespace(begin_nested=lambda: _Savepoint())
        created_user = SimpleNamespace(id=1)

        with (
            patch.object(auth_module.user_repository, "exists_any", new=AsyncMock(return_value=False)),
            patch.object(auth_module.AuthService, "hash_password", new=AsyncMock(return_value="hashed-password")) as hash_password,
            patch.object(auth_module.user_repository, "create", new=AsyncMock(return_value=created_user)) as create_user,
        ):
            user = await auth_module.auth_service.ensure_default_admin(
                session,
                username="admin",
                password="StrongAdmin123!",
                email="admin@example.com",
            )

        self.assertIs(user, created_user)
        hash_password.assert_awaited_once_with("StrongAdmin123!")
        create_user.assert_awaited_once_with(
            session,
            username="admin",
            email="admin@example.com",
            password_hash="hashed-password",
            role="admin",
        )

    async def test_ensure_default_admin_returns_none_for_insecure_password(self) -> None:
        session = SimpleNamespace(begin_nested=lambda: None)

        with patch.object(auth_module.user_repository, "exists_any", new=AsyncMock(return_value=False)):
            result = await auth_module.auth_service.ensure_default_admin(
                session,
                username="admin",
                password="admin",
                email="admin@example.com",
            )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
