#!/usr/bin/env python3
#
# app/utils/hidden_captcha.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Invisible, no-interaction anti-bot validation for public auth forms."""

from __future__ import annotations

import base64
import binascii
import hmac
from enum import StrEnum
from hashlib import sha256
from time import time

HONEYPOT_FIELD_NAME = "website"
_SALT = "caddybuddy-hidden-captcha"
_TOKEN_VERSION = "v1"
DEFAULT_MAX_AGE_SECONDS = 6 * 60 * 60
DEFAULT_MIN_AGE_SECONDS = 1.0


class CaptchaOutcome(StrEnum):
    """The outcome of a hidden CAPTCHA check."""

    OK = "ok"
    HONEYPOT_FILLED = "honeypot_filled"
    MISSING = "missing"
    INVALID = "invalid"
    EXPIRED = "expired"
    TOO_FAST = "too_fast"


def _sign(secret_key: str, payload: str) -> str:
    return hmac.new(
        f"{secret_key}:{_SALT}".encode(), payload.encode("utf-8"), sha256
    ).hexdigest()


def issue_captcha_token(secret_key: str) -> str:
    """Mint a signed, timestamped token for an authentication form."""
    payload = f"{_TOKEN_VERSION}:{int(time())}"
    signature = _sign(secret_key, payload)
    raw = f"{payload}:{signature}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def verify_captcha_token(
    *,
    token: str | None,
    honeypot: str | None,
    secret_key: str,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: float | None = None,
) -> CaptchaOutcome:
    """Accept only empty, signed, current and plausibly human submissions."""
    if honeypot is not None and honeypot.strip():
        return CaptchaOutcome.HONEYPOT_FILLED
    if not token:
        return CaptchaOutcome.MISSING

    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload, signature = raw.rsplit(":", 1)
        version, issued_at_raw = payload.split(":", 1)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return CaptchaOutcome.INVALID

    if version != _TOKEN_VERSION or not hmac.compare_digest(signature, _sign(secret_key, payload)):
        return CaptchaOutcome.INVALID

    try:
        issued_at = int(issued_at_raw)
    except ValueError:
        return CaptchaOutcome.INVALID

    age_seconds = (now if now is not None else time()) - issued_at
    if age_seconds < 0:
        return CaptchaOutcome.INVALID
    if age_seconds > max_age_seconds:
        return CaptchaOutcome.EXPIRED
    if min_age_seconds > 0 and age_seconds < min_age_seconds:
        return CaptchaOutcome.TOO_FAST
    return CaptchaOutcome.OK
