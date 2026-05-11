#!/usr/bin/env python3
#
# app/services/auth.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import asyncio
import hmac
import logging
from functools import cache
from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe

import bcrypt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.models.entities import ApiKey, User
from app.repositories.api_keys import api_key_repository
from app.repositories.users import user_repository


_DUMMY_BCRYPT_HASH = "$2b$12$XoxrmnloUyPG.UR9bJMmh.jZY3PuHalwrTlwknAY8hcepqC8VZ0.K"
_MIN_PASSWORD_LENGTH = 12
_API_KEY_PREFIX_LENGTH = 8
_API_KEY_NAME_MAX_LENGTH = 120
_ALLOWED_API_KEY_PERMISSIONS = frozenset({"read", "write", "delete"})

logger = logging.getLogger(__name__)


@cache
def _secret_key_bytes() -> bytes:
    return get_settings().secret_key.get_secret_value().encode("utf-8")


class WeakPasswordError(ValueError):
    """Raised when a password does not satisfy the minimum policy."""


class AuthorizationError(Exception):
    """Raised when an actor lacks required privileges for an action."""


class AuthService:
    """Stateless authentication service exposed as the module-level singleton."""

    @classmethod
    def _hmac_digest(cls, scope: str, value: str) -> bytes:
        """Return a fixed 32-byte HMAC-SHA256 digest scoped by ``scope``.

        The fixed output length is intentional so the password path stays well
        under bcrypt's 72-byte input limit and cannot be silently truncated.
        """
        message = scope.encode("utf-8") + b"\0" + value.encode("utf-8")
        return hmac.digest(_secret_key_bytes(), message, sha256)

    @classmethod
    async def hash_password(cls, password: str) -> str:
        """Return a peppered bcrypt hash of the password."""
        peppered = cls._hmac_digest("password", password)
        return await asyncio.to_thread(
            lambda: bcrypt.hashpw(peppered, bcrypt.gensalt()).decode("utf-8")
        )

    @classmethod
    async def verify_password(cls, password: str, password_hash: str) -> bool:
        """Verify a password against a peppered bcrypt hash."""
        peppered = cls._hmac_digest("password", password)
        return await asyncio.to_thread(
            lambda: bcrypt.checkpw(peppered, password_hash.encode("utf-8"))
        )

    @classmethod
    def hash_api_key(cls, raw_key: str) -> str:
        """Return a deterministic peppered SHA-256 hash of an API key."""
        return sha256(cls._hmac_digest("api-key", raw_key)).hexdigest()

    @classmethod
    def verify_api_key(cls, raw_key: str, key_hash: str) -> bool:
        """Verify an API key using constant-time comparison."""
        return hmac.compare_digest(cls.hash_api_key(raw_key), key_hash)

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

    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.role != "admin":
            raise AuthorizationError("Administrator privileges are required.")

    @staticmethod
    def _require_self_or_admin(actor: User, subject_user_id: int) -> None:
        if actor.role != "admin" and actor.id != subject_user_id:
            raise AuthorizationError("Insufficient privileges for this action.")

    @staticmethod
    def _validate_api_key_request(
        name: str,
        permissions: dict[str, bool],
        expires_at: datetime | None,
    ) -> str:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("API key name cannot be empty.")
        if len(normalized_name) > _API_KEY_NAME_MAX_LENGTH:
            raise ValueError(
                f"API key name must not exceed {_API_KEY_NAME_MAX_LENGTH} characters."
            )
        if expires_at is not None:
            if expires_at.tzinfo is None:
                raise ValueError("expires_at must be timezone-aware.")
            if expires_at <= datetime.now(UTC):
                raise ValueError("expires_at must be in the future.")

        unexpected_permissions = set(permissions).difference(_ALLOWED_API_KEY_PERMISSIONS)
        if unexpected_permissions:
            raise ValueError(
                "permissions contains unsupported keys: "
                f"{', '.join(sorted(unexpected_permissions))}"
            )
        for permission_name, enabled in permissions.items():
            if not isinstance(enabled, bool):
                raise ValueError(f"permissions.{permission_name} must be a boolean.")
        return normalized_name

    async def authenticate(self, session: AsyncSession, username: str, password: str) -> User | None:
        """Authenticate by username and password with timing-safe negative checks."""
        user = await user_repository.get_by_username(session, username)
        if user is None or not user.is_active:
            logger.debug("User not found or inactive: %r", username)
            await self.verify_password(password, _DUMMY_BCRYPT_HASH)
            return None
        verified = await self.verify_password(password, user.password_hash)
        if not verified:
            logger.info("Authentication attempt failed")
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
        if not get_settings().allow_insecure_defaults:
            self._validate_password_strength(password)
        if await user_repository.count(session) > 0:
            return None
        try:
            return await user_repository.create(
                session,
                username=username,
                email=email,
                password_hash=await self.hash_password(password),
                role="admin",
            )
        except IntegrityError:
            await session.rollback()
            return None

    async def create_user(
        self,
        session: AsyncSession,
        *,
        actor: User,
        username: str,
        email: str | None,
        password: str,
        role: str,
    ) -> User:
        """Create a user after validating password strength."""
        self._require_admin(actor)
        self._validate_password_strength(password)
        return await user_repository.create(
            session,
            username=username,
            email=email,
            password_hash=await self.hash_password(password),
            role=role,
        )

    async def update_password(
        self,
        session: AsyncSession,
        *,
        actor: User,
        user: User,
        new_password: str,
    ) -> User:
        """Replace a user's password after validating strength."""
        self._require_self_or_admin(actor, user.id)
        self._validate_password_strength(new_password)
        return await user_repository.update_password(
            session,
            user,
            await self.hash_password(new_password),
        )

    async def create_api_key(
        self,
        session: AsyncSession,
        *,
        actor: User,
        user_id: int,
        name: str,
        permissions: dict[str, bool],
        expires_at: datetime | None,
    ) -> tuple[ApiKey, str]:
        """Create an API key and return the persisted record plus the raw secret."""
        self._require_self_or_admin(actor, user_id)
        normalized_name = self._validate_api_key_request(name, permissions, expires_at)
        raw_key = token_urlsafe(32)
        key_prefix = raw_key[:_API_KEY_PREFIX_LENGTH]
        api_key = await api_key_repository.create(
            session,
            name=normalized_name,
            key_prefix=key_prefix,
            key_hash=self.hash_api_key(raw_key),
            user_id=user_id,
            permissions=permissions,
            expires_at=expires_at,
        )
        return api_key, raw_key


auth_service = AuthService()