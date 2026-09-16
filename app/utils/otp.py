#!/usr/bin/env python3
#
# app/utils/otp.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""TOTP, provisioning QR-code, and recovery-code helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import json
import re
import secrets
import time
from urllib.parse import quote, urlencode

import qrcode

_TOTP_CODE_RE = re.compile(r"^\d{6}$", re.ASCII)
_RECOVERY_CODE_RE = re.compile(r"^[a-f0-9]{16}$", re.ASCII)
_RECOVERY_HASH_RE = re.compile(r"^[a-f0-9]{64}$", re.ASCII)


def generate_totp_secret() -> str:
    """Return a 160-bit Base32 secret suitable for RFC 6238 TOTP."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def build_provisioning_uri(secret: str, username: str, *, issuer: str) -> str:
    """Build an ``otpauth://`` URI for an authenticator application."""
    label = quote(f"{issuer}:{username}", safe="")
    query = urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": 6, "period": 30})
    return f"otpauth://totp/{label}?{query}"


def provisioning_qr_data_url(provisioning_uri: str) -> str:
    """Encode a provisioning URI as an in-memory PNG data URL."""
    image = qrcode.make(provisioning_uri)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(output.getvalue()).decode('ascii')}"


def verify_totp(secret: str, code: str, *, now: float | None = None) -> int | None:
    """Return the accepted time-step counter for a current six-digit TOTP code."""
    normalized_code = code.strip()
    if _TOTP_CODE_RE.fullmatch(normalized_code) is None:
        return None
    try:
        padded_secret = secret.strip().upper() + "=" * (-len(secret.strip()) % 8)
        key = base64.b32decode(padded_secret, casefold=False)
    except (ValueError, binascii.Error):
        return None

    counter = int(time.time() if now is None else now) // 30
    message = counter.to_bytes(8, "big", signed=False)
    digest = hmac.new(key, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    expected = str((int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000).zfill(6)
    return counter if hmac.compare_digest(normalized_code, expected) else None


def generate_recovery_codes(*, count: int = 8) -> list[str]:
    """Generate high-entropy, single-use recovery codes."""
    if not 1 <= count <= 32:
        raise ValueError("Recovery-code count must be between 1 and 32.")
    return [secrets.token_hex(8) for _ in range(count)]


def serialize_recovery_codes(codes: list[str], *, key: bytes) -> str:
    """Return a JSON list of keyed recovery-code hashes for storage."""
    hashed = []
    for code in codes:
        normalized = code.strip().lower()
        if _RECOVERY_CODE_RE.fullmatch(normalized) is None:
            raise ValueError("Invalid recovery code.")
        hashed.append(hmac.digest(key, normalized.encode("ascii"), hashlib.sha256).hex())
    return json.dumps(hashed, separators=(",", ":"))


def consume_recovery_code(candidate: str, stored_json: str | None, *, key: bytes) -> tuple[bool, str]:
    """Verify a recovery code and return its replacement hashed-code list."""
    try:
        stored = json.loads(stored_json or "[]")
    except (TypeError, json.JSONDecodeError):
        stored = []
    stored_hashes = [item for item in stored if isinstance(item, str) and _RECOVERY_HASH_RE.fullmatch(item)]
    normalized = candidate.strip().lower()
    if _RECOVERY_CODE_RE.fullmatch(normalized) is None:
        return False, json.dumps(stored_hashes, separators=(",", ":"))

    candidate_hash = hmac.digest(key, normalized.encode("ascii"), hashlib.sha256).hex()
    remaining: list[str] = []
    found = False
    for stored_hash in stored_hashes:
        if not found and hmac.compare_digest(candidate_hash, stored_hash):
            found = True
            continue
        remaining.append(stored_hash)
    return found, json.dumps(remaining, separators=(",", ":"))
