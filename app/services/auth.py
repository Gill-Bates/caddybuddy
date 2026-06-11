#!/usr/bin/env python3
#
# app/services/auth.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import hmac
import logging
from datetime import UTC, datetime
from functools import cache
from hashlib import sha256

import bcrypt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import (
    get_settings,
    _INSECURE_ADMIN_PASSWORD_VALUES,
    _MIN_ADMIN_PASSWORD_LENGTH,
)
from app.models.entities import User
from app.repositories.users import user_repository


_DUMMY_BCRYPT_HASH = "$2b$12$XoxrmnloUyPG.UR9bJMmh.jZY3PuHalwrTlwknAY8hcepqC8VZ0.K"
_MIN_PASSWORD_LENGTH = 8
_MAX_PASSWORD_LENGTH = 4096
_BCRYPT_CONCURRENCY = 4
_bcrypt_semaphore = asyncio.Semaphore(_BCRYPT_CONCURRENCY)

logger = logging.getLogger(__name__)


@cache
def _password_pepper_bytes() -> bytes:
    settings = get_settings()
    pepper = settings.password_pepper
    if pepper is not None:
        return pepper.get_secret_value().encode("utf-8")
    return settings.secret_key.get_secret_value().encode("utf-8")


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
    def _validate_password_input(cls, password: str) -> None:
        if not isinstance(password, str):
            raise ValueError("Password must be a string.")
        if len(password) > _MAX_PASSWORD_LENGTH:
            raise ValueError(f"Password must not exceed {_MAX_PASSWORD_LENGTH} characters.")

    @classmethod
    async def _hash_password_unchecked(cls, password: str) -> str:
        peppered = cls._hmac_digest("password", password)
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
        peppered = cls._hmac_digest("password", password)

        def _check() -> bool:
            try:
                return bcrypt.checkpw(peppered, password_hash.encode("utf-8"))
            except ValueError:
                logger.warning("Invalid bcrypt password hash encountered")
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

    async def authenticate(self, session: AsyncSession, username: str, password: str) -> User | None:
        """Authenticate by username and password with timing-safe negative checks.

        The caller owns the transaction commit.
        """
        try:
            self._validate_password_input(password)
        except ValueError:
            logger.info("Authentication attempt rejected due to invalid password input")
            return None

        user = await user_repository.get_by_username(session, username)
        if user is None or not user.is_active:
            logger.debug("User not found or inactive: %r", username)
            await self.verify_password(password, _DUMMY_BCRYPT_HASH)
            return None
        verified = await self.verify_password(password, user.password_hash)
        if not verified:
            return None
        await user_repository.update_last_login(session, user, datetime.now(UTC))
        return user

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
            raise ValueError(
                "Set CB_ADMIN_PASSWORD, CADDYBUDDY_ADMIN_PASSWORD, or ADMIN_PASSWORD to a non-default value "
                f"of at least {_MIN_ADMIN_PASSWORD_LENGTH} characters before first startup."
            )
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