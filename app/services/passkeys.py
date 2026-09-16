#!/usr/bin/env python3
#
# app/services/passkeys.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""WebAuthn (passkey) registration and authentication.

Ceremony challenges are persisted (see ``app.models.entities.PasskeyChallenge``)
rather than kept in the session or in process memory, so a ceremony survives a
worker switch and consuming a challenge is one atomic, non-replayable delete.

The Relying Party ID and expected origin come from ``CB_PASSKEY_RP_ID`` /
``CB_PUBLIC_ORIGIN`` when configured, otherwise they are derived from the
request. Passkeys are bound to the RP ID, so changing the deployment hostname
invalidates every registered credential.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self
from urllib.parse import urlsplit

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config.settings import get_settings
from app.models.entities import Passkey, User
from app.repositories.passkeys import (
    DuplicateChallengeError,
    DuplicatePasskeyError,
    passkey_repository,
)
from app.repositories.users import user_repository

logger = logging.getLogger(__name__)

MAX_PASSKEYS_PER_USER = 10
CHALLENGE_TTL_SECONDS = 300
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_MAX_CLIENT_DATA_BYTES = 4096
_MAX_TRANSPORTS = 8


class PasskeyError(RuntimeError):
    """Base class for passkey ceremony failures."""


class PasskeyConfigurationError(PasskeyError):
    """Raised when the Relying Party cannot be resolved for this request."""


class PasskeyLimitError(PasskeyError):
    """Raised when a user already holds the maximum number of passkeys."""


class PasskeyChallengeError(PasskeyError):
    """Raised when a ceremony challenge is missing, expired, or already used."""


class PasskeyVerificationError(PasskeyError):
    """Raised when the authenticator response fails verification."""


@dataclass(frozen=True, slots=True)
class RelyingParty:
    """The WebAuthn Relying Party identity a ceremony is bound to."""

    rp_id: str
    origin: str
    name: str

    @classmethod
    def resolve(cls, request: Request) -> Self:
        """Derive the Relying Party from configuration, falling back to the request."""
        settings = get_settings()
        origin = settings.public_origin or _request_origin(request)
        host = urlsplit(origin).hostname
        if not host:
            raise PasskeyConfigurationError("Could not determine the request host.")
        return cls(
            rp_id=settings.passkey_rp_id or host.rstrip(".").lower(),
            origin=origin,
            name=settings.app_name,
        )


def _request_origin(request: Request) -> str:
    """Return the request's own origin, rejecting insecure non-loopback contexts."""
    scheme = (request.url.scheme or "").lower()
    host = (request.url.hostname or "").rstrip(".").lower()
    if scheme not in {"http", "https"} or not host:
        raise PasskeyConfigurationError("Could not determine the request origin.")
    if scheme != "https" and host not in _LOOPBACK_HOSTS:
        raise PasskeyConfigurationError(
            "Passkeys require HTTPS. Serve CaddyBuddy over HTTPS or set CB_PUBLIC_ORIGIN."
        )

    port = request.url.port
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        port = None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{scheme}://{netloc}"


def _user_handle(user_id: int) -> bytes:
    """Return an opaque, stable WebAuthn user handle for an internal user ID.

    WebAuthn requires ``user.id`` to carry no personal data, and a sequential
    integer would leak account information to the authenticator.
    """
    secret = get_settings().secret_key.get_secret_value().encode("utf-8")
    return hmac.new(secret, f"passkey-user:{user_id}".encode(), hashlib.sha256).digest()


def _decode_base64url(value: str, *, limit: int) -> bytes:
    padded = value + "=" * ((-len(value)) % 4)
    if len(padded) > limit:
        raise ValueError("value is too large")
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise ValueError("value is not valid base64url") from exc


def _client_data_challenge(credential: dict[str, Any]) -> str:
    """Extract the challenge the authenticator signed over."""
    response = credential.get("response")
    raw = response.get("clientDataJSON") if isinstance(response, dict) else None
    if not isinstance(raw, str) or not raw:
        raise PasskeyVerificationError("The authenticator response is missing clientDataJSON.")

    try:
        client_data = json.loads(_decode_base64url(raw, limit=_MAX_CLIENT_DATA_BYTES))
    except (ValueError, json.JSONDecodeError) as exc:
        raise PasskeyVerificationError("The authenticator response is malformed.") from exc

    if not isinstance(client_data, dict):
        raise PasskeyVerificationError("The authenticator response is malformed.")
    challenge = client_data.get("challenge")
    if not isinstance(challenge, str) or not challenge:
        raise PasskeyVerificationError("The authenticator response is missing a challenge.")
    return challenge


def _credential_id(credential: dict[str, Any]) -> str:
    credential_id = credential.get("id")
    if not isinstance(credential_id, str) or not credential_id:
        raise PasskeyVerificationError("The authenticator response is missing a credential ID.")
    return credential_id


def _serialize_credential_transports(credential: dict[str, Any]) -> str | None:
    """Return the authenticator's advertised transports as a JSON array.

    Transport hints are advisory metadata reported by the browser, so unknown
    values are dropped rather than failing the registration.
    """
    response = credential.get("response")
    raw = response.get("transports") if isinstance(response, dict) else None
    if not isinstance(raw, list):
        return None

    transports: list[str] = []
    for item in raw[:_MAX_TRANSPORTS]:
        if not isinstance(item, str):
            continue
        try:
            transports.append(AuthenticatorTransport(item).value)
        except ValueError:
            logger.debug("Ignoring unknown passkey transport hint.")
    if not transports:
        return None
    return json.dumps(transports, separators=(",", ":"))


def parse_transports(raw: str | None) -> list[str]:
    """Return the stored transport hints for display, tolerating bad data."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed[:_MAX_TRANSPORTS] if isinstance(item, str) and item]


class PasskeyService:
    """Drive both WebAuthn ceremonies.

    Callers commit issued challenges and successful credential mutations.
    Consumed challenges are committed immediately so later verification
    failures cannot make them replayable.
    """

    @staticmethod
    async def _issue_challenge(
        session: AsyncSession,
        *,
        challenge: bytes,
        ceremony: str,
        user_id: int | None,
        now: datetime,
    ) -> None:
        await passkey_repository.purge_expired_challenges(session, now)
        try:
            await passkey_repository.store_challenge(
                session,
                challenge=bytes_to_base64url(challenge),
                ceremony=ceremony,
                user_id=user_id,
                expires_at=now + timedelta(seconds=CHALLENGE_TTL_SECONDS),
            )
        except DuplicateChallengeError as exc:
            raise PasskeyChallengeError("Could not issue a challenge. Please retry.") from exc

    @staticmethod
    async def _consume_challenge(
        session: AsyncSession,
        *,
        challenge: str,
        ceremony: str,
    ) -> int | None:
        consumed, user_id = await passkey_repository.consume_challenge(
            session,
            challenge=challenge,
            ceremony=ceremony,
            now=datetime.now(UTC),
        )
        if not consumed:
            raise PasskeyChallengeError("The passkey challenge is invalid or has expired.")
        # Burn the one-time challenge before later verification can fail and roll back.
        await session.commit()
        return user_id

    async def begin_registration(
        self,
        session: AsyncSession,
        request: Request,
        user: User,
    ) -> dict[str, Any]:
        """Return creation options for ``navigator.credentials.create()``."""
        relying_party = RelyingParty.resolve(request)
        if await passkey_repository.count_for_user(session, user.id) >= MAX_PASSKEYS_PER_USER:
            raise PasskeyLimitError(
                f"A maximum of {MAX_PASSKEYS_PER_USER} passkeys is supported. "
                "Remove an existing passkey first."
            )

        existing_credential_ids = await passkey_repository.credential_ids_for_user(session, user.id)
        options = generate_registration_options(
            rp_id=relying_party.rp_id,
            rp_name=relying_party.name,
            user_id=_user_handle(user.id),
            user_name=user.username,
            user_display_name=user.username,
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=_decode_base64url(credential_id, limit=1024))
                for credential_id in existing_credential_ids
            ]
            or None,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
        )
        await self._issue_challenge(
            session,
            challenge=options.challenge,
            ceremony="registration",
            user_id=user.id,
            now=datetime.now(UTC),
        )
        logger.info("Passkey registration started for username=%r", user.username)
        return json.loads(options_to_json(options))

    async def finish_registration(
        self,
        session: AsyncSession,
        request: Request,
        user: User,
        *,
        credential: dict[str, Any],
        device_name: str | None = None,
    ) -> Passkey:
        """Verify a registration response and persist the new credential."""
        relying_party = RelyingParty.resolve(request)
        challenge = _client_data_challenge(credential)
        challenge_user_id = await self._consume_challenge(
            session,
            challenge=challenge,
            ceremony="registration",
        )
        if challenge_user_id != user.id:
            logger.warning(
                "Passkey registration challenge belonged to user_id=%s, not %s.",
                challenge_user_id,
                user.id,
            )
            raise PasskeyChallengeError("The passkey challenge was not issued for this account.")

        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=_decode_base64url(challenge, limit=1024),
                expected_rp_id=relying_party.rp_id,
                expected_origin=relying_party.origin,
            )
        except Exception as exc:
            logger.warning("Passkey registration verification failed: %s", type(exc).__name__)
            raise PasskeyVerificationError("The passkey could not be verified.") from exc

        try:
            passkey = await passkey_repository.create(
                session,
                user_id=user.id,
                credential_id=bytes_to_base64url(verified.credential_id),
                public_key=verified.credential_public_key,
                sign_count=verified.sign_count,
                device_name=device_name,
                transports=_serialize_credential_transports(credential),
            )
        except DuplicatePasskeyError as exc:
            raise PasskeyVerificationError("This passkey is already registered.") from exc
        except ValueError as exc:
            raise PasskeyVerificationError(str(exc)) from exc

        logger.info(
            "Passkey registered for username=%r passkey_id=%s",
            user.username,
            passkey.id,
        )
        return passkey

    async def begin_authentication(
        self,
        session: AsyncSession,
        request: Request,
    ) -> dict[str, Any]:
        """Return request options for a usernameless (discoverable) sign-in."""
        relying_party = RelyingParty.resolve(request)
        options = generate_authentication_options(
            rp_id=relying_party.rp_id,
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        await self._issue_challenge(
            session,
            challenge=options.challenge,
            ceremony="authentication",
            user_id=None,
            now=datetime.now(UTC),
        )
        return json.loads(options_to_json(options))

    async def finish_authentication(
        self,
        session: AsyncSession,
        request: Request,
        *,
        credential: dict[str, Any],
    ) -> User:
        """Verify an assertion and return the authenticated user."""
        relying_party = RelyingParty.resolve(request)
        challenge = _client_data_challenge(credential)
        credential_id = _credential_id(credential)
        await self._consume_challenge(session, challenge=challenge, ceremony="authentication")

        passkey = await passkey_repository.get_by_credential_id(session, credential_id)
        if passkey is None:
            logger.warning("Passkey sign-in used an unknown credential.")
            raise PasskeyVerificationError("This passkey is not registered.")

        user = await user_repository.get_by_id(session, passkey.user_id)
        if user is None or not user.is_active:
            raise PasskeyVerificationError("This account cannot sign in.")

        try:
            verified = verify_authentication_response(
                credential=credential,
                expected_challenge=_decode_base64url(challenge, limit=1024),
                expected_rp_id=relying_party.rp_id,
                expected_origin=relying_party.origin,
                credential_public_key=passkey.public_key,
                credential_current_sign_count=passkey.sign_count,
            )
        except Exception as exc:
            logger.warning(
                "Passkey verification failed for username=%r: %s",
                user.username,
                type(exc).__name__,
            )
            raise PasskeyVerificationError("The passkey could not be verified.") from exc

        recorded = await passkey_repository.record_authentication(
            session,
            passkey,
            new_sign_count=verified.new_sign_count,
            used_at=datetime.now(UTC),
        )
        if not recorded:
            logger.error(
                "Rejected passkey sign-in for username=%r: signature counter did not advance "
                "(stored=%s, presented=%s).",
                user.username,
                passkey.sign_count,
                verified.new_sign_count,
            )
            raise PasskeyVerificationError("The passkey could not be verified.")

        logger.info("Passkey sign-in verified for username=%r", user.username)
        return user

    async def list_for_user(self, session: AsyncSession, user: User) -> Sequence[Passkey]:
        return await passkey_repository.list_for_user(session, user.id)

    async def delete_for_user(
        self,
        session: AsyncSession,
        user: User,
        passkey_id: int,
    ) -> bool:
        deleted = await passkey_repository.delete_for_user(
            session,
            passkey_id=passkey_id,
            user_id=user.id,
        )
        if deleted:
            logger.info("Passkey %s removed for username=%r", passkey_id, user.username)
        return deleted

    async def any_registered(self, session: AsyncSession) -> bool:
        return await passkey_repository.exists_any(session)


passkey_service = PasskeyService()
