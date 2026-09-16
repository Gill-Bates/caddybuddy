#!/usr/bin/env python3
#
# app/repositories/passkeys.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    Passkey,
    PasskeyChallenge,
    _normalize_ceremony,
    _normalize_challenge,
    _normalize_credential_id,
)


class DuplicatePasskeyError(ValueError):
    """Raised when a credential ID is already registered."""


class DuplicateChallengeError(ValueError):
    """Raised when a challenge value is already stored (replay)."""


class PasskeyRepository:
    """Persistence for WebAuthn credentials and their ceremony challenges."""

    async def list_for_user(self, session: AsyncSession, user_id: int) -> Sequence[Passkey]:
        result = await session.execute(
            select(Passkey)
            .where(Passkey.user_id == user_id)
            .order_by(Passkey.created_at.desc(), Passkey.id.desc())
        )
        return result.scalars().all()

    async def count_for_user(self, session: AsyncSession, user_id: int) -> int:
        result = await session.execute(
            select(func.count()).select_from(Passkey).where(Passkey.user_id == user_id)
        )
        return int(result.scalar_one())

    async def credential_ids_for_user(self, session: AsyncSession, user_id: int) -> list[str]:
        result = await session.execute(
            select(Passkey.credential_id).where(Passkey.user_id == user_id)
        )
        return list(result.scalars().all())

    async def exists_any(self, session: AsyncSession) -> bool:
        result = await session.execute(select(Passkey.id).limit(1))
        return result.scalar_one_or_none() is not None

    async def get_by_credential_id(
        self,
        session: AsyncSession,
        credential_id: str,
    ) -> Passkey | None:
        try:
            normalized_credential_id = _normalize_credential_id(credential_id)
        except ValueError:
            return None
        result = await session.execute(
            select(Passkey).where(Passkey.credential_id == normalized_credential_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        credential_id: str,
        public_key: bytes,
        sign_count: int,
        device_name: str | None = None,
        transports: str | None = None,
    ) -> Passkey:
        if not isinstance(public_key, bytes) or not public_key:
            raise ValueError("public_key must be non-empty bytes.")
        passkey = Passkey(
            user_id=user_id,
            credential_id=credential_id,
            public_key=public_key,
            sign_count=sign_count,
            device_name=device_name,
            transports=transports,
        )
        session.add(passkey)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise DuplicatePasskeyError("Credential is already registered.") from exc
        return passkey

    async def delete_for_user(
        self,
        session: AsyncSession,
        *,
        passkey_id: int,
        user_id: int,
    ) -> bool:
        """Delete a passkey, scoped to its owner so IDs cannot be probed."""
        result = await session.execute(
            delete(Passkey).where(Passkey.id == passkey_id, Passkey.user_id == user_id)
        )
        return bool(result.rowcount)

    async def record_authentication(
        self,
        session: AsyncSession,
        passkey: Passkey,
        *,
        new_sign_count: int,
        used_at: datetime,
    ) -> bool:
        """Store a strictly increasing signature counter and the usage timestamp.

        A regression (or a replayed assertion) leaves the counter untouched and
        returns False. Authenticators that always report 0 are allowed, since
        WebAuthn permits omitting the counter entirely.
        """
        if new_sign_count < 0:
            raise ValueError("new_sign_count must be non-negative.")

        condition = (
            Passkey.sign_count == 0
            if new_sign_count == 0
            else Passkey.sign_count < new_sign_count
        )
        result = await session.execute(
            update(Passkey)
            .where(Passkey.id == passkey.id, condition)
            .values(sign_count=new_sign_count, last_used_at=used_at)
        )
        if not result.rowcount:
            return False
        passkey.sign_count = new_sign_count
        passkey.last_used_at = used_at
        return True

    async def store_challenge(
        self,
        session: AsyncSession,
        *,
        challenge: str,
        ceremony: str,
        user_id: int | None,
        expires_at: datetime,
    ) -> PasskeyChallenge:
        normalized_ceremony = _normalize_ceremony(ceremony)
        if normalized_ceremony == "registration" and user_id is None:
            raise ValueError("A registration challenge requires a user_id.")

        record = PasskeyChallenge(
            challenge=_normalize_challenge(challenge),
            ceremony=normalized_ceremony,
            user_id=user_id,
            expires_at=expires_at,
        )
        session.add(record)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise DuplicateChallengeError("Challenge already exists.") from exc
        return record

    async def consume_challenge(
        self,
        session: AsyncSession,
        *,
        challenge: str,
        ceremony: str,
        now: datetime,
    ) -> tuple[bool, int | None]:
        """Atomically delete an unexpired challenge and return its bound user.

        Returns ``(False, None)`` when the challenge is unknown, expired, was
        issued for a different ceremony, or has already been consumed.
        """
        normalized_ceremony = _normalize_ceremony(ceremony)
        try:
            normalized_challenge = _normalize_challenge(challenge)
        except ValueError:
            return (False, None)

        result = await session.execute(
            delete(PasskeyChallenge)
            .where(
                PasskeyChallenge.challenge == normalized_challenge,
                PasskeyChallenge.ceremony == normalized_ceremony,
                PasskeyChallenge.expires_at > now,
            )
            .returning(PasskeyChallenge.user_id)
        )
        row = result.first()
        if row is None:
            return (False, None)
        return (True, row[0])

    async def purge_expired_challenges(self, session: AsyncSession, now: datetime) -> int:
        result = await session.execute(
            delete(PasskeyChallenge).where(PasskeyChallenge.expires_at <= now)
        )
        return int(result.rowcount or 0)


passkey_repository = PasskeyRepository()
