#!/usr/bin/env python3
#
# app/services/auth.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import base64
import hmac
import logging
from datetime import UTC, datetime
from functools import cache
from hashlib import sha256

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import (
    _INSECURE_ADMIN_PASSWORD_VALUES,
    _MIN_ADMIN_PASSWORD_LENGTH,
    get_settings,
)
from app.models.entities import User
from app.repositories.users import user_repository
from app.utils.otp import (
    build_provisioning_uri,
    consume_recovery_code,
    generate_recovery_codes,
    generate_totp_secret,
    serialize_recovery_codes,
    verify_totp,
)

_DUMMY_BCRYPT_HASH = "$2b$12$XoxrmnloUyPG.UR9bJMmh.jZY3PuHalwrTlwknAY8hcepqC8VZ0.K"
_MIN_PASSWORD_LENGTH = 8
_MAX_PASSWORD_LENGTH = 4096
_BCRYPT_CONCURRENCY = 4
_bcrypt_semaphore = asyncio.Semaphore(_BCRYPT_CONCURRENCY)

PASSWORD_MIN_LENGTH = _MIN_PASSWORD_LENGTH
PASSWORD_MAX_LENGTH = _MAX_PASSWORD_LENGTH
PASSWORD_POLICY_MESSAGE = (
    f"Password must be at least {PASSWORD_MIN_LENGTH} characters long and contain uppercase, lowercase, digit, and special character."
)

logger = logging.getLogger(__name__)


@cache
def _password_pepper_bytes() -> bytes:
    settings = get_settings()
    pepper = settings.password_pepper
    if pepper is not None:
        return pepper.get_secret_value().encode("utf-8")
    return settings.secret_key.get_secret_value().encode("utf-8")


@cache
def _otp_fernet() -> Fernet:
    """Return the Fernet instance used to encrypt TOTP secrets at rest."""
    key = hmac.digest(_password_pepper_bytes(), b"caddybuddy-otp-fernet-v1", sha256)
    return Fernet(base64.urlsafe_b64encode(key))


@cache
def _recovery_code_key() -> bytes:
    """Derive a domain-separated key for recovery-code hashes."""
    return hmac.digest(_password_pepper_bytes(), b"caddybuddy-otp-recovery-v1", sha256)


class WeakPasswordError(ValueError):
    """Raised when a password does not satisfy the minimum policy."""


class AuthService:
    """Stateless authentication service exposed as the module-level singleton."""

    @classmethod
    def _hmac_digest(cls, scope: str, value: str) -> bytes:
        """Return a fixed 32-byte HMAC-SHA256 digest scoped by ``scope``.

        The fixed output length is intentional so the password path stays well
        under bcrypt's 72-byte input limit and cannot be silently truncated.
        """
        message = scope.encode("utf-8") + b"\0" + value.encode("utf-8")
        return hmac.digest(_password_pepper_bytes(), message, sha256)

    @classmethod
    def _bcrypt_password_bytes(cls, scope: str, value: str) -> bytes:
        """Return NUL-free bytes suitable for bcrypt input."""
        return base64.b64encode(cls._hmac_digest(scope, value))

    @classmethod
    def _legacy_bcrypt_password_candidates(cls, scope: str, value: str) -> tuple[bytes, ...]:
        """Return historical bcrypt inputs used before digest encoding was NUL-safe."""
        digest = cls._hmac_digest(scope, value)
        if b"\x00" not in digest:
            return (digest,)
        return (digest, digest.split(b"\x00", 1)[0])

    @classmethod
    def _validate_password_input(cls, password: str) -> None:
        if not isinstance(password, str):
            raise TypeError("Password must be a string.")
        if len(password) > _MAX_PASSWORD_LENGTH:
            raise ValueError(f"Password must not exceed {_MAX_PASSWORD_LENGTH} characters.")

    @classmethod
    async def _hash_password_unchecked(cls, password: str) -> str:
        peppered = cls._bcrypt_password_bytes("password", password)
        async with _bcrypt_semaphore:
            return await asyncio.to_thread(
                lambda: bcrypt.hashpw(peppered, bcrypt.gensalt()).decode("utf-8")
            )

    @classmethod
    async def hash_password(cls, password: str) -> str:
        """Return a peppered bcrypt hash of a policy-compliant password."""
        cls._validate_password_input(password)
        cls._validate_password_strength(password)
        return await cls._hash_password_unchecked(password)

    @classmethod
    async def verify_password(cls, password: str, password_hash: str) -> bool:
        """Verify a password against a peppered bcrypt hash."""
        cls._validate_password_input(password)
        peppered = cls._bcrypt_password_bytes("password", password)
        legacy_candidates = cls._legacy_bcrypt_password_candidates("password", password)

        def _check() -> bool:
            encoded_hash = password_hash.encode("utf-8")
            try:
                if bcrypt.checkpw(peppered, encoded_hash):
                    return True
            except ValueError:
                logger.warning("Invalid bcrypt password hash encountered")
                return False
            for legacy_peppered in legacy_candidates:
                try:
                    if bcrypt.checkpw(legacy_peppered, encoded_hash):
                        return True
                except ValueError:
                    continue
            return False

        async with _bcrypt_semaphore:
            return await asyncio.to_thread(_check)

    @classmethod
    def _validate_password_strength(cls, password: str) -> None:
        if len(password) < _MIN_PASSWORD_LENGTH:
            raise WeakPasswordError(
                f"Password must be at least {_MIN_PASSWORD_LENGTH} characters long."
            )
        if not any(character.islower() for character in password):
            raise WeakPasswordError("Password must contain at least one lowercase letter.")
        if not any(character.isupper() for character in password):
            raise WeakPasswordError("Password must contain at least one uppercase letter.")
        if not any(character.isdigit() for character in password):
            raise WeakPasswordError("Password must contain at least one digit.")
        if not any(not character.isalnum() for character in password):
            raise WeakPasswordError("Password must contain at least one special character.")

    async def authenticate(
        self,
        session: AsyncSession,
        username: str,
        password: str,
        *,
        update_last_login: bool = True,
    ) -> User | None:
        """Authenticate by username and password with timing-safe negative checks.

        The caller owns the transaction commit.
        """
        try:
            self._validate_password_input(password)
        except (TypeError, ValueError):
            logger.info("Authentication attempt rejected due to invalid password input")
            return None

        try:
            user = await user_repository.get_by_username(session, username)
        except ValueError:
            logger.debug("User not found (username failed normalization): %r", username)
            await self.verify_password(password, _DUMMY_BCRYPT_HASH)
            return None
        if user is None or not user.is_active:
            logger.debug("User not found or inactive: %r", username)
            await self.verify_password(password, _DUMMY_BCRYPT_HASH)
            return None
        verified = await self.verify_password(password, user.password_hash)
        if not verified:
            return None
        if update_last_login:
            await user_repository.update_last_login(session, user, datetime.now(UTC))
        return user

    @staticmethod
    def _encrypt_otp_secret(secret: str) -> str:
        return _otp_fernet().encrypt(secret.encode("ascii")).decode("ascii")

    @staticmethod
    def _decrypt_otp_secret(encrypted_secret: str | None) -> str | None:
        if not encrypted_secret:
            return None
        try:
            return _otp_fernet().decrypt(encrypted_secret.encode("ascii")).decode("ascii")
        except (InvalidToken, UnicodeDecodeError):
            logger.error("Could not decrypt stored OTP secret.")
            return None

    async def begin_otp_enrollment(self, session: AsyncSession, user: User) -> str | None:
        """Create and persist a pending encrypted TOTP secret for ``user``."""
        if user.otp_enabled:
            return None
        secret = generate_totp_secret()
        if not await user_repository.start_otp_enrollment(session, user, self._encrypt_otp_secret(secret)):
            return None
        return secret

    @staticmethod
    def provisioning_uri(secret: str, username: str) -> str:
        return build_provisioning_uri(secret, username, issuer="CaddyBuddy")

    def pending_otp_secret(self, user: User) -> str | None:
        """Return the decrypted pending enrollment secret, never an enabled one."""
        if user.otp_enabled:
            return None
        return self._decrypt_otp_secret(user.otp_secret)

    async def confirm_otp_enrollment(
        self,
        session: AsyncSession,
        user: User,
        code: str,
    ) -> list[str] | None:
        """Enable a pending OTP secret and return one-time recovery codes."""
        if user.otp_enabled:
            return None
        secret = self._decrypt_otp_secret(user.otp_secret)
        if secret is None:
            return None
        counter = verify_totp(secret, code)
        if counter is None:
            return None
        recovery_codes = generate_recovery_codes()
        serialized_codes = serialize_recovery_codes(recovery_codes, key=_recovery_code_key())
        if not await user_repository.confirm_otp_enrollment(
            session,
            user,
            encrypted_secret=user.otp_secret or "",
            recovery_codes=serialized_codes,
            counter=counter,
        ):
            return None
        return recovery_codes

    async def verify_otp_factor(self, session: AsyncSession, user: User, code: str) -> str | None:
        """Verify a TOTP or one-time recovery code and atomically consume it."""
        if not user.otp_enabled:
            return None
        secret = self._decrypt_otp_secret(user.otp_secret)
        counter = verify_totp(secret, code) if secret is not None else None
        if counter is not None:
            if await user_repository.consume_otp_counter(session, user, counter):
                return "totp"
            return None

        success, remaining_codes = consume_recovery_code(
            code,
            user.otp_recovery_codes,
            key=_recovery_code_key(),
        )
        if not success:
            return None
        if await user_repository.consume_recovery_code(
            session,
            user,
            previous_codes=user.otp_recovery_codes,
            remaining_codes=remaining_codes,
        ):
            return "recovery"
        return None

    async def disable_otp(self, session: AsyncSession, user: User) -> bool:
        """Disable OTP and remove its secret, recovery codes, and replay marker."""
        return await user_repository.disable_otp(session, user)

    async def ensure_default_admin(
        self,
        session: AsyncSession,
        *,
        username: str,
        password: str,
        email: str,
    ) -> User | None:
        """Create the default admin when the database has no users yet."""
        if await user_repository.exists_any(session):
            return None
        password = password.strip()
        if password in _INSECURE_ADMIN_PASSWORD_VALUES or len(password) < _MIN_ADMIN_PASSWORD_LENGTH:
            return None
        password_hash = await self.hash_password(password)
        try:
            async with session.begin_nested():
                return await user_repository.create(
                    session,
                    username=username,
                    email=email,
                    password_hash=password_hash,
                    role="admin",
                )
        except IntegrityError:
            return None


auth_service = AuthService()
