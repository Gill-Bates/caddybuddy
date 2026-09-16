#!/usr/bin/env python3
#
# tests/test_otp.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import base64
import hashlib
import hmac
import unittest

from app.utils.otp import (
    consume_recovery_code,
    generate_recovery_codes,
    generate_totp_secret,
    provisioning_qr_data_url,
    serialize_recovery_codes,
    verify_totp,
)


def _totp_code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return str((int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000).zfill(6)


class OtpUtilityTests(unittest.TestCase):
    def test_generated_totp_secret_verifies_a_current_code(self) -> None:
        secret = generate_totp_secret()
        counter = 1_234_567

        self.assertEqual(verify_totp(secret, _totp_code(secret, counter), now=counter * 30), counter)

    def test_totp_rejects_adjacent_time_step(self) -> None:
        secret = generate_totp_secret()
        counter = 1_234_567

        self.assertIsNone(verify_totp(secret, _totp_code(secret, counter - 1), now=counter * 30))

    def test_provisioning_qr_is_a_png_data_url(self) -> None:
        qr_data_url = provisioning_qr_data_url("otpauth://totp/CaddyBuddy:admin?secret=ABC&issuer=CaddyBuddy")

        self.assertTrue(qr_data_url.startswith("data:image/png;base64,"))

    def test_recovery_code_is_hashed_and_consumed_once(self) -> None:
        key = b"unit-test-recovery-key"
        codes = generate_recovery_codes()
        stored = serialize_recovery_codes(codes, key=key)

        self.assertNotIn(codes[0], stored)
        used, remaining = consume_recovery_code(codes[0], stored, key=key)
        used_again, _unchanged = consume_recovery_code(codes[0], remaining, key=key)

        self.assertTrue(used)
        self.assertFalse(used_again)
        self.assertEqual(len(remaining), len(stored) - 67)


if __name__ == "__main__":
    unittest.main()
