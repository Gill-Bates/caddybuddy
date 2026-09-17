#!/usr/bin/env python3
#
# tests/test_otp_flow.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""End-to-end TOTP flows against a real temporary SQLite database."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.limiter import limiter
from app.config.settings import get_settings
from app.utils.hidden_captcha import CaptchaOutcome
from tests.env_overrides import ModuleEnv

_ENV = ModuleEnv()

from fastapi.testclient import TestClient

import app.services.auth as auth_module
from app.models.base import Base
from app.models.entities import User
from app.repositories.users import user_repository
from app.routers.ui.auth import router as auth_router
from app.services.auth import auth_service
from tests.ui_test_app import build_ui_test_app

_USERNAME = "admin"
_PASSWORD = "Password123!"


def tearDownModule() -> None:
    _ENV.restore()
    _clear_key_caches()


def _clear_key_caches() -> None:
    auth_module._password_pepper_bytes.cache_clear()
    auth_module._otp_fernet.cache_clear()
    auth_module._recovery_code_key.cache_clear()


def _totp_code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return str((int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000).zfill(6)


class _FixedClock:
    """Controls the TOTP time step without touching the global ``time`` module."""

    def __init__(self) -> None:
        self.counter = int(time.time()) // 30

    def time(self) -> float:
        return self.counter * 30 + 5.0


class _OtpDatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    """Temporary SQLite database with one admin account and a controllable TOTP clock."""

    async def asyncSetUp(self) -> None:
        _ENV.apply()
        _clear_key_caches()

        self._temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self._temp_dir.name) / "otp.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

        self.clock = _FixedClock()
        self._clock_patcher = patch("app.utils.otp.time", new=SimpleNamespace(time=self.clock.time))
        self._clock_patcher.start()

        async with self.session_factory() as session:
            user = await user_repository.create(
                session,
                username=_USERNAME,
                email=None,
                password_hash=await auth_service.hash_password(_PASSWORD),
                role="admin",
            )
            await session.commit()
            self.user_id = user.id

    async def asyncTearDown(self) -> None:
        self._clock_patcher.stop()
        await self.engine.dispose()
        self._temp_dir.cleanup()
        get_settings.cache_clear()
        _clear_key_caches()

    async def _load_user(self, session: AsyncSession) -> User:
        user = await user_repository.get_by_id(session, self.user_id)
        assert user is not None
        return user

    async def _enroll(self) -> tuple[str, list[str]]:
        async with self.session_factory() as session:
            user = await self._load_user(session)
            secret = await auth_service.begin_otp_enrollment(session, user)
            await session.commit()
        self.assertIsNotNone(secret)

        async with self.session_factory() as session:
            user = await self._load_user(session)
            self.assertFalse(user.otp_enabled)
            self.assertEqual(auth_service.pending_otp_secret(user), secret)
            self.assertNotEqual(user.otp_secret, secret, "OTP secret must be encrypted at rest.")
            recovery_codes = await auth_service.confirm_otp_enrollment(
                session, user, _totp_code(secret, self.clock.counter)
            )
            await session.commit()
        self.assertIsNotNone(recovery_codes)
        return secret, recovery_codes

    async def _verify(self, code: str) -> str | None:
        async with self.session_factory() as session:
            user = await self._load_user(session)
            method = await auth_service.verify_otp_factor(session, user, code)
            await session.commit()
        return method


class OtpFlowIntegrationTests(_OtpDatabaseTestCase):
    async def test_enrollment_rejects_wrong_confirmation_code(self) -> None:
        async with self.session_factory() as session:
            user = await self._load_user(session)
            secret = await auth_service.begin_otp_enrollment(session, user)
            await session.commit()

        async with self.session_factory() as session:
            user = await self._load_user(session)
            stale_code = _totp_code(secret, self.clock.counter - 1)
            self.assertIsNone(await auth_service.confirm_otp_enrollment(session, user, stale_code))
            await session.commit()

        async with self.session_factory() as session:
            user = await self._load_user(session)
            self.assertFalse(user.otp_enabled)
            self.assertIsNone(user.otp_recovery_codes)

    async def test_enrolled_totp_code_is_single_use(self) -> None:
        secret, _recovery_codes = await self._enroll()

        # The confirmation already consumed the current step.
        self.assertIsNone(await self._verify(_totp_code(secret, self.clock.counter)))

        self.clock.counter += 1
        next_code = _totp_code(secret, self.clock.counter)
        self.assertEqual(await self._verify(next_code), "totp")
        self.assertIsNone(await self._verify(next_code))

    async def test_recovery_codes_are_hashed_and_single_use(self) -> None:
        _secret, recovery_codes = await self._enroll()

        async with self.session_factory() as session:
            stored = (await self._load_user(session)).otp_recovery_codes or ""
        for code in recovery_codes:
            self.assertNotIn(code, stored)

        self.assertEqual(await self._verify(recovery_codes[0].upper()), "recovery")
        self.assertIsNone(await self._verify(recovery_codes[0]))
        self.assertEqual(await self._verify(recovery_codes[1]), "recovery")

    async def test_disable_removes_all_otp_credentials(self) -> None:
        secret, recovery_codes = await self._enroll()

        async with self.session_factory() as session:
            user = await self._load_user(session)
            self.assertTrue(await auth_service.disable_otp(session, user))
            await session.commit()

        async with self.session_factory() as session:
            user = await self._load_user(session)
            self.assertFalse(user.otp_enabled)
            self.assertIsNone(user.otp_secret)
            self.assertIsNone(user.otp_recovery_codes)
            self.assertIsNone(user.otp_last_verified_counter)

        self.clock.counter += 1
        self.assertIsNone(await self._verify(_totp_code(secret, self.clock.counter)))
        self.assertIsNone(await self._verify(recovery_codes[0]))


class OtpLoginIntegrationTests(_OtpDatabaseTestCase):
    """Drive the browser login routes against the same real database."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self._captcha_patcher = patch("app.routers.ui.auth.verify_captcha_token", return_value=CaptchaOutcome.OK)
        self._captcha_patcher.start()
        self._limiter_enabled = limiter.enabled
        limiter.enabled = False

    async def asyncTearDown(self) -> None:
        limiter.enabled = self._limiter_enabled
        self._captcha_patcher.stop()
        await super().asyncTearDown()

    def _build_client(self) -> TestClient:
        session_factory = self.session_factory

        async def _session_override():
            async with session_factory() as session:
                yield session

        app = build_ui_test_app(auth_router, session_override=_session_override, stub_routes=[])
        return TestClient(app)

    @staticmethod
    def _csrf_token(html: str) -> str:
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if match is None:
            raise AssertionError("csrf_token input not found")
        return match.group(1)

    def _submit_password(self, client: TestClient):
        login_page = client.get("/login")
        return client.post(
            "/login",
            data={
                "username": _USERNAME,
                "password": _PASSWORD,
                "next": "/sites",
                "csrf_token": self._csrf_token(login_page.text),
            },
            follow_redirects=False,
        )

    def _submit_code(self, client: TestClient, code: str):
        otp_page = client.get("/login/otp", follow_redirects=False)
        self.assertEqual(otp_page.status_code, 200)
        return client.post(
            "/login/otp",
            data={"code": code, "csrf_token": self._csrf_token(otp_page.text)},
            follow_redirects=False,
        )

    async def test_login_requires_fresh_second_factor(self) -> None:
        secret, recovery_codes = await self._enroll()
        self.clock.counter += 1
        code = _totp_code(secret, self.clock.counter)

        with self._build_client() as client:
            password_step = self._submit_password(client)
            self.assertEqual(password_step.headers["location"], "/login/otp")
            accepted = self._submit_code(client, code)
        self.assertEqual(accepted.status_code, 303)
        self.assertEqual(accepted.headers["location"], "/sites")

        async with self.session_factory() as session:
            self.assertIsNotNone((await self._load_user(session)).last_login)

        with self._build_client() as client:
            self._submit_password(client)
            replayed = self._submit_code(client, code)
            self.assertEqual(replayed.status_code, 403)
            self.assertIn("Invalid authentication code.", replayed.text)

            recovered = self._submit_code(client, recovery_codes[0])
        self.assertEqual(recovered.status_code, 303)
        self.assertEqual(recovered.headers["location"], "/sites")

        with self._build_client() as client:
            self._submit_password(client)
            reused_recovery = self._submit_code(client, recovery_codes[0])
        self.assertEqual(reused_recovery.status_code, 403)


if __name__ == "__main__":
    unittest.main()
