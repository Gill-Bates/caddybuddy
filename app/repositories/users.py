#!/usr/bin/env python3
#
# app/repositories/users.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import datetime
import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import User


_BCRYPT_HASH_RE = re.compile(r"^\$2[aby]\$(?P<cost>\d{2})\$[./A-Za-z0-9]{53}$")
_MIN_BCRYPT_COST = 12
_MAX_BCRYPT_COST = 31
_VALID_USER_ROLES = frozenset({"admin", "user"})


class DuplicateUserError(ValueError):
    """Raised when a user collides with an existing username or email."""


def _normalize_username(username: str) -> str:
    normalized = username.strip()
    if not normalized:
        raise ValueError("Username must not be empty.")
    return normalized


def _normalize_email(email: str | None) -> str | None:
    if email is None:
        return None
    normalized = email.strip().lower()
    return normalized or None


def _validate_password_hash(password_hash: str) -> str:
    normalized = password_hash.strip()
    match = _BCRYPT_HASH_RE.fullmatch(normalized)
    if match is None:
        raise ValueError("password_hash must be a bcrypt hash.")
    cost = int(match.group("cost"))
    if cost < _MIN_BCRYPT_COST or cost > _MAX_BCRYPT_COST:
        raise ValueError(
            f"bcrypt cost must be between {_MIN_BCRYPT_COST} and {_MAX_BCRYPT_COST}."
        )
    return normalized


def _validate_user_role(role: str) -> str:
    normalized = role.strip().lower()
    if normalized not in _VALID_USER_ROLES:
        raise ValueError(f"Invalid user role: {role!r}. Must be one of: {', '.join(sorted(_VALID_USER_ROLES))}.")
    return normalized


def _is_duplicate_user_integrity_error(exc: IntegrityError) -> bool:
    message = " ".join(
        part.lower()
        for part in (
            str(exc.orig or ""),
            str(exc.statement or ""),
            str(exc),
        )
        if part
    )
    return any(token in message for token in ("users.username", "users.email")) and any(
        token in message for token in ("unique", "duplicate", "constraint")
    )


class UserRepository:
    async def count(self, session: AsyncSession) -> int:
        result = await session.execute(select(func.count()).select_from(User))
        return int(result.scalar_one())

    async def exists_any(self, session: AsyncSession) -> bool:
        result = await session.execute(select(User.id).limit(1))
        return result.scalar_one_or_none() is not None

    async def get_by_id(self, session: AsyncSession, user_id: int) -> User | None:
        return await session.get(User, user_id)

    async def get_by_username(self, session: AsyncSession, username: str) -> User | None:
        normalized_username = _normalize_username(username)
        result = await session.execute(
            select(User).where(User.username == normalized_username)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        *,
        username: str,
        email: str | None,
        password_hash: str,
        role: str = "user",
        is_active: bool = True,
    ) -> User:
        normalized_username = _normalize_username(username)
        normalized_email = _normalize_email(email)
        normalized_password_hash = _validate_password_hash(password_hash)
        normalized_role = _validate_user_role(role)

        user = User(
            username=normalized_username,
            email=normalized_email,
            password_hash=normalized_password_hash,
            role=normalized_role,
            is_active=is_active,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            if _is_duplicate_user_integrity_error(exc):
                raise DuplicateUserError("User already exists.") from exc
            raise
        return user

    async def update_last_login(
        self,
        session: AsyncSession,
        user: User,
        when: datetime,
    ) -> User:
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("last_login must be timezone-aware.")
        user.last_login = when
        await session.flush()
        return user

    async def update_password(
        self,
        session: AsyncSession,
        user: User,
        password_hash: str,
    ) -> User:
        """Update user's password hash."""
        user.password_hash = _validate_password_hash(password_hash)
        await session.flush()
        return user


user_repository = UserRepository()