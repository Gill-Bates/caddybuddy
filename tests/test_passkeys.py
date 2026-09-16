#!/usr/bin/env python3
#
# tests/test_passkeys.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.limiter import limiter
from app.config.settings import get_settings

_ENV_OVERRIDES = {
    "CB_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key-for-testing",
}
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}

for key, value in _ENV_OVERRIDES.items():
    os.environ[key] = value

get_settings.cache_clear()

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.database.session import get_db_session
from app.models.base import Base
from app.repositories.passkeys import DuplicatePasskeyError, passkey_repository
from app.repositories.users import user_repository
from app.routers.passkeys import _require_api_user
from app.routers.passkeys import router as passkeys_router
from app.services.passkeys import (
    PasskeyChallengeError,
    PasskeyVerificationError,
    _client_data_challenge,
    _serialize_credential_transports,
    parse_transports,
    passkey_service,
)


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


def _client_data(challenge: object) -> str:
    raw = json.dumps({"type": "webauthn.get", "challenge": challenge}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class PasskeyRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self._temp_dir.name) / 'passkeys.db'}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self.now = datetime.now(UTC)
        async with self.session_factory() as session:
            owner = await user_repository.create(session, username="owner", email=None, password_hash="$2b$12$" + "a" * 53)
            other = await user_repository.create(session, username="other", email=None, password_hash="$2b$12$" + "b" * 53)
            await session.commit()
        self.owner_id = owner.id
        self.other_id = other.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._temp_dir.cleanup()

    async def _store_challenge(self, challenge: str, *, ceremony: str, expires_in: int = 300) -> None:
        async with self.session_factory() as session:
            await passkey_repository.store_challenge(
                session,
                challenge=challenge,
                ceremony=ceremony,
                user_id=self.owner_id,
                expires_at=self.now + timedelta(seconds=expires_in),
            )
            await session.commit()

    async def _consume(self, challenge: str, *, ceremony: str) -> tuple[bool, int | None]:
        async with self.session_factory() as session:
            outcome = await passkey_repository.consume_challenge(
                session, challenge=challenge, ceremony=ceremony, now=self.now
            )
            await session.commit()
        return outcome

    async def _create_passkey(self, credential_id: str, *, user_id: int, sign_count: int = 0) -> int:
        async with self.session_factory() as session:
            passkey = await passkey_repository.create(
                session,
                user_id=user_id,
                credential_id=credential_id,
                public_key=b"public-key",
                sign_count=sign_count,
            )
            await session.commit()
        return passkey.id

    async def test_challenge_is_consumed_exactly_once_for_its_ceremony(self) -> None:
        await self._store_challenge("challenge-one", ceremony="registration")

        self.assertEqual(await self._consume("challenge-one", ceremony="authentication"), (False, None))
        self.assertEqual(await self._consume("challenge-one", ceremony="registration"), (True, self.owner_id))
        self.assertEqual(await self._consume("challenge-one", ceremony="registration"), (False, None))

    async def test_expired_or_malformed_challenge_is_rejected(self) -> None:
        await self._store_challenge("expired-challenge", ceremony="authentication", expires_in=-1)

        self.assertEqual(await self._consume("expired-challenge", ceremony="authentication"), (False, None))
        self.assertEqual(await self._consume("not base64url!", ceremony="authentication"), (False, None))

    async def test_registration_challenge_requires_a_user(self) -> None:
        async with self.session_factory() as session:
            with self.assertRaises(ValueError):
                await passkey_repository.store_challenge(
                    session,
                    challenge="anonymous",
                    ceremony="registration",
                    user_id=None,
                    expires_at=self.now + timedelta(seconds=300),
                )

    async def test_signature_counter_must_advance(self) -> None:
        passkey_id = await self._create_passkey("credential-counter", user_id=self.owner_id, sign_count=5)

        async with self.session_factory() as session:
            passkey = await passkey_repository.get_by_credential_id(session, "credential-counter")
            self.assertEqual(passkey.id, passkey_id)
            self.assertFalse(
                await passkey_repository.record_authentication(session, passkey, new_sign_count=5, used_at=self.now)
            )
            self.assertTrue(
                await passkey_repository.record_authentication(session, passkey, new_sign_count=6, used_at=self.now)
            )
            await session.commit()

        async with self.session_factory() as session:
            stored = await passkey_repository.get_by_credential_id(session, "credential-counter")
        self.assertEqual(stored.sign_count, 6)
        self.assertIsNotNone(stored.last_used_at)

    async def test_zero_counter_authenticators_stay_usable(self) -> None:
        await self._create_passkey("credential-zero", user_id=self.owner_id)

        async with self.session_factory() as session:
            passkey = await passkey_repository.get_by_credential_id(session, "credential-zero")
            self.assertTrue(
                await passkey_repository.record_authentication(session, passkey, new_sign_count=0, used_at=self.now)
            )

    async def test_duplicate_credential_is_rejected(self) -> None:
        await self._create_passkey("credential-dup", user_id=self.owner_id)

        with self.assertRaises(DuplicatePasskeyError):
            await self._create_passkey("credential-dup", user_id=self.other_id)

    async def test_delete_is_scoped_to_the_owner(self) -> None:
        passkey_id = await self._create_passkey("credential-owned", user_id=self.owner_id)

        async with self.session_factory() as session:
            self.assertFalse(
                await passkey_repository.delete_for_user(session, passkey_id=passkey_id, user_id=self.other_id)
            )
            self.assertTrue(
                await passkey_repository.delete_for_user(session, passkey_id=passkey_id, user_id=self.owner_id)
            )
            await session.commit()
            self.assertFalse(await passkey_repository.exists_any(session))


class PasskeyServiceHelperTests(unittest.TestCase):
    def test_client_data_challenge_is_extracted(self) -> None:
        credential = {"response": {"clientDataJSON": _client_data("abc_DEF-123")}}

        self.assertEqual(_client_data_challenge(credential), "abc_DEF-123")

    def test_malformed_client_data_is_rejected(self) -> None:
        for response in (
            {},
            {"clientDataJSON": "%%%"},
            {"clientDataJSON": base64.urlsafe_b64encode(b"[]").decode().rstrip("=")},
            {"clientDataJSON": _client_data("")},
            {"clientDataJSON": "A" * 8000},
        ):
            with self.subTest(response=str(response)[:40]), self.assertRaises(PasskeyVerificationError):
                _client_data_challenge({"response": response})

    def test_unknown_transport_hints_are_dropped(self) -> None:
        credential = {"response": {"transports": ["usb", "carrier-pigeon", 7, "internal"]}}

        self.assertEqual(_serialize_credential_transports(credential), '["usb","internal"]')
        self.assertIsNone(_serialize_credential_transports({"response": {"transports": ["bogus"]}}))

    def test_stored_transports_tolerate_bad_data(self) -> None:
        self.assertEqual(parse_transports('["usb","nfc"]'), ["usb", "nfc"])
        for raw in (None, "", "not json", '{"usb": true}'):
            with self.subTest(raw=raw):
                self.assertEqual(parse_transports(raw), [])


class PasskeyServiceChallengeTests(unittest.IsolatedAsyncioTestCase):
    async def test_consumed_challenge_is_committed_immediately(self) -> None:
        session = AsyncMock()

        with patch(
            "app.services.passkeys.passkey_repository.consume_challenge",
            new=AsyncMock(return_value=(True, 7)),
        ):
            user_id = await passkey_service._consume_challenge(
                session,
                challenge="challenge",
                ceremony="registration",
            )

        self.assertEqual(user_id, 7)
        session.commit.assert_awaited_once_with()

    async def test_rejected_challenge_is_not_committed(self) -> None:
        session = AsyncMock()

        with (
            patch(
                "app.services.passkeys.passkey_repository.consume_challenge",
                new=AsyncMock(return_value=(False, None)),
            ),
            self.assertRaises(PasskeyChallengeError),
        ):
            await passkey_service._consume_challenge(
                session,
                challenge="challenge",
                ceremony="registration",
            )

        session.commit.assert_not_awaited()


class PasskeyLoginRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        for key, value in _ENV_OVERRIDES.items():
            os.environ[key] = value
        get_settings.cache_clear()
        self._limiter_enabled = limiter.enabled
        limiter.enabled = False

    def tearDown(self) -> None:
        limiter.enabled = self._limiter_enabled
        get_settings.cache_clear()

    def _finish_login(self, user: SimpleNamespace, *, next_url: str = "/sites"):
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="unit-test-secret-key-for-testing")
        app.include_router(passkeys_router)

        async def _session_override():
            yield AsyncMock()

        app.dependency_overrides[get_db_session] = _session_override
        captured: dict[str, object] = {}

        @app.get("/_session")
        async def _read_session(request: Request) -> dict[str, object]:
            return dict(request.session)

        with (
            patch("app.routers.passkeys.passkey_service.finish_authentication", new=AsyncMock(return_value=user)),
            patch("app.routers.passkeys.user_repository.update_last_login", new=AsyncMock()) as update_last_login,
            TestClient(app) as client,
        ):
            response = client.post(
                "/api/passkeys/login/finish",
                json={"credential": {"id": "credential"}, "next_url": next_url},
            )
            captured.update(client.get("/_session").json())
        return response, captured, update_last_login

    def test_passkey_login_with_totp_still_requires_the_second_factor(self) -> None:
        user = SimpleNamespace(
            id=1,
            username="admin",
            password_hash="hash",
            otp_secret="encrypted-secret",
            otp_enabled=True,
            is_active=True,
        )

        response, session_data, update_last_login = self._finish_login(user)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["redirect_url"], "/login/otp")
        self.assertNotIn("user_id", session_data)
        self.assertEqual(session_data["otp_pending_user_id"], 1)
        self.assertEqual(session_data["otp_pending_next"], "/sites")
        update_last_login.assert_not_awaited()

    def test_passkey_login_without_totp_issues_a_session_and_sanitizes_next(self) -> None:
        user = SimpleNamespace(
            id=1,
            username="admin",
            password_hash="hash",
            otp_secret=None,
            otp_enabled=False,
            is_active=True,
        )

        response, session_data, update_last_login = self._finish_login(user, next_url="https://evil.example/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["redirect_url"], "/")
        self.assertEqual(session_data["user_id"], 1)
        update_last_login.assert_awaited_once()


class PasskeyRegistrationRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._limiter_enabled = limiter.enabled
        limiter.enabled = False

    def tearDown(self) -> None:
        limiter.enabled = self._limiter_enabled

    @staticmethod
    def _app_for(user: SimpleNamespace) -> FastAPI:
        app = FastAPI()
        app.include_router(passkeys_router)

        async def _session_override():
            yield AsyncMock()

        async def _user_override():
            return user

        app.dependency_overrides[get_db_session] = _session_override
        app.dependency_overrides[_require_api_user] = _user_override
        return app

    def test_registration_start_rejects_an_incorrect_current_password(self) -> None:
        user = SimpleNamespace(id=1, username="admin", password_hash="stored-hash")
        app = self._app_for(user)

        with (
            patch("app.routers.passkeys.auth_service.verify_password", new=AsyncMock(return_value=False)) as verify,
            patch("app.routers.passkeys.passkey_service.begin_registration", new=AsyncMock()) as begin,
            TestClient(app) as client,
        ):
            response = client.post(
                "/api/passkeys/register/start",
                json={"current_password": "wrong-password"},
            )

        self.assertEqual(response.status_code, 400)
        verify.assert_awaited_once_with("wrong-password", "stored-hash")
        begin.assert_not_awaited()

    def test_registration_start_accepts_the_current_password(self) -> None:
        user = SimpleNamespace(id=1, username="admin", password_hash="stored-hash")
        app = self._app_for(user)

        with (
            patch("app.routers.passkeys.auth_service.verify_password", new=AsyncMock(return_value=True)) as verify,
            patch(
                "app.routers.passkeys.passkey_service.begin_registration",
                new=AsyncMock(return_value={"challenge": "challenge"}),
            ) as begin,
            TestClient(app) as client,
        ):
            response = client.post(
                "/api/passkeys/register/start",
                json={"current_password": "correct-password"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"options": {"challenge": "challenge"}})
        verify.assert_awaited_once_with("correct-password", "stored-hash")
        begin.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
