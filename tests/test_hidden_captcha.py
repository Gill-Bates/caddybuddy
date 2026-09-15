#!/usr/bin/env python3
#
# tests/test_hidden_captcha.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.utils.hidden_captcha import (
    DEFAULT_MAX_AGE_SECONDS,
    CaptchaOutcome,
    issue_captcha_token,
    verify_captcha_token,
)


class HiddenCaptchaTests(unittest.TestCase):
    secret_key = "unit-test-secret-key"
    issued_at = 1_700_000_000

    def setUp(self) -> None:
        with patch("app.utils.hidden_captcha.time", return_value=self.issued_at):
            self.token = issue_captcha_token(self.secret_key)

    def test_accepts_empty_honeypot_with_a_current_signed_token(self) -> None:
        outcome = verify_captcha_token(
            token=self.token,
            honeypot="",
            secret_key=self.secret_key,
            now=self.issued_at + 1,
        )

        self.assertEqual(outcome, CaptchaOutcome.OK)

    def test_rejects_a_filled_honeypot_without_exposing_the_signal(self) -> None:
        outcome = verify_captcha_token(
            token=self.token,
            honeypot="https://spam.example",
            secret_key=self.secret_key,
            now=self.issued_at + 1,
        )

        self.assertEqual(outcome, CaptchaOutcome.HONEYPOT_FILLED)

    def test_rejects_tampered_or_missing_tokens(self) -> None:
        for token, expected in (("", CaptchaOutcome.MISSING), ("not-a-token", CaptchaOutcome.INVALID)):
            with self.subTest(token=token):
                outcome = verify_captcha_token(
                    token=token,
                    honeypot="",
                    secret_key=self.secret_key,
                    now=self.issued_at + 1,
                )
                self.assertEqual(outcome, expected)

    def test_enforces_the_signed_token_time_window(self) -> None:
        cases = (
            (self.issued_at, CaptchaOutcome.TOO_FAST),
            (self.issued_at + DEFAULT_MAX_AGE_SECONDS + 1, CaptchaOutcome.EXPIRED),
        )
        for now, expected in cases:
            with self.subTest(now=now):
                outcome = verify_captcha_token(
                    token=self.token,
                    honeypot="",
                    secret_key=self.secret_key,
                    now=now,
                )
                self.assertEqual(outcome, expected)


if __name__ == "__main__":
    unittest.main()
