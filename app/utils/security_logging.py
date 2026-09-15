#!/usr/bin/env python3
#
# app/utils/security_logging.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Stable, injection-safe security events for external log consumers."""

from __future__ import annotations

import json
import logging
from ipaddress import ip_address
from typing import Literal

from fastapi import Request

AuthenticationFailureReason = Literal[
    "anti_bot_rejected",
    "invalid_credentials",
    "password_too_long",
    "rate_limited",
    "username_too_long",
]

logger = logging.getLogger("caddybuddy.security")
_UNKNOWN_CLIENT_IP = "unknown"
_MISSING_USERNAME = "-"
_MAX_LOGGED_USERNAME_LENGTH = 64


def _normalized_client_ip(request: Request) -> str:
    """Return the Uvicorn-resolved client address as a canonical IP literal.

    Uvicorn has already applied its trusted-proxy policy before the request reaches
    the application. Forwarded headers must not be parsed again here because doing
    so would let untrusted clients spoof the address consumed by banning tools.
    """
    if request.client is None:
        return _UNKNOWN_CLIENT_IP

    try:
        return ip_address(request.client.host).compressed
    except ValueError:
        return _UNKNOWN_CLIENT_IP


def _encoded_username(username: str | None) -> str:
    if username is None:
        return json.dumps(_MISSING_USERNAME)
    if len(username) > _MAX_LOGGED_USERNAME_LENGTH:
        username = f"{username[:_MAX_LOGGED_USERNAME_LENGTH]}..."
    return json.dumps(username, ensure_ascii=True)


def log_authentication_failure(
    request: Request,
    *,
    username: str | None,
    reason: AuthenticationFailureReason,
    status_code: Literal[403, 429],
) -> None:
    """Emit one parseable authentication failure without trusting raw headers."""
    logger.warning(
        "SECURITY authentication_failed client_ip=%s username=%s "
        "reason=%s status_code=%d",
        _normalized_client_ip(request),
        _encoded_username(username),
        reason,
        status_code,
    )
