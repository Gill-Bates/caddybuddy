#!/usr/bin/env python3
#
# tests/test_settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config.settings import DEFAULT_CADDY_ADMIN_URL, DEFAULT_CADDYFILE_PATH, Settings


def _settings_kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "CADDYBUDDY_SECRET_KEY": "StrongSecretKey-1234567890",
        "CADDYBUDDY_ADMIN_PASSWORD": "StrongAdminPassword-123!",
    }
    values.update(overrides)
    return values


class SettingsValidationTests(unittest.TestCase):
    def test_caddy_admin_url_is_normalized(self) -> None:
        settings = Settings(**_settings_kwargs(caddy_admin_url="http://localhost:2019/"))

        self.assertEqual(settings.caddy_admin_url, "http://localhost:2019")

    def test_caddy_admin_url_rejects_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "caddy_admin_url must not include a path"):
            Settings(**_settings_kwargs(caddy_admin_url="http://localhost:2019/config/"))

    def test_caddy_api_url_rejects_credentials(self) -> None:
        with self.assertRaisesRegex(ValueError, "caddy_api_url must not include username or password"):
            Settings(**_settings_kwargs(caddy_api_url="http://user:pass@localhost"))

    def test_caddy_admin_url_rejects_credentials(self) -> None:
        with self.assertRaisesRegex(ValueError, "caddy_admin_url must not include username or password"):
            Settings(**_settings_kwargs(caddy_admin_url="http://user:pass@localhost:2019"))

    def test_caddy_admin_api_path_is_normalized(self) -> None:
        settings = Settings(**_settings_kwargs(CADDYBUDDY_CADDY_ADMIN_API_PATH=" load "))

        self.assertEqual(settings.caddy_admin_api_path, "/load")

    def test_caddy_admin_api_path_rejects_query_and_relative_segments(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain query or fragment"):
            Settings(**_settings_kwargs(CADDYBUDDY_CADDY_ADMIN_API_PATH="/load?x=1"))
        with self.assertRaisesRegex(ValueError, "Only the Caddy /load endpoint is supported"):
            Settings(**_settings_kwargs(CADDYBUDDY_CADDY_ADMIN_API_PATH="/config/"))

    def test_default_admin_email_is_stripped_and_validated(self) -> None:
        settings = Settings(**_settings_kwargs(default_admin_email=" admin@example.com "))

        self.assertEqual(settings.default_admin_email, "admin@example.com")

    def test_default_admin_email_rejects_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "Default admin email must be a valid email address"):
            Settings(**_settings_kwargs(default_admin_email="not-an-email"))

    def test_legacy_mixed_case_secret_alias_is_not_accepted(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CB_SECRET_KEY": "",
                "CADDYBUDDY_SECRET_KEY": "",
                "SECRET_KEY": "",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "Set a strong secret key"):
                Settings(
                    caddybuddy_SECRET_KEY="StrongSecretKey-1234567890",
                    CADDYBUDDY_ADMIN_PASSWORD="StrongAdminPassword-123!",
                )

    def test_legacy_caddy_runtime_env_aliases_are_ignored(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CB_CADDY_API_URL": "http://host.docker.internal:2019",
                "CB_CADDYFILE_PATH": "/etc/caddy/Caddyfile",
            },
            clear=True,
        ):
            settings = Settings(
                CB_SECRET_KEY="StrongSecretKey-1234567890",
                CB_ADMIN_PASSWORD="StrongAdminPassword-123!",
            )

        self.assertEqual(settings.caddy_admin_url, DEFAULT_CADDY_ADMIN_URL)
        self.assertEqual(settings.caddy_api_url, DEFAULT_CADDY_ADMIN_URL)
        self.assertEqual(settings.mounted_caddyfile_path, DEFAULT_CADDYFILE_PATH)

    def test_explicit_admin_bootstrap_password_is_allowed(self) -> None:
        settings = Settings(
            CB_SECRET_KEY="StrongSecretKey-1234567890",
            CB_ADMIN_PASSWORD="admin",
        )

        self.assertEqual(settings.default_admin_password.get_secret_value(), "admin")

    def test_ssllabs_url_is_normalized_and_email_env_is_ignored(self) -> None:
        settings = Settings(
            **_settings_kwargs(
                CADDYBUDDY_SSLLABS_EMAIL=" security@example.com ",
                CADDYBUDDY_SSLLABS_API_BASE_URL="https://api.ssllabs.com/api/v4/",
            )
        )

        self.assertEqual(settings.ssllabs_api_base_url, "https://api.ssllabs.com/api/v4")
        self.assertFalse(hasattr(settings, "ssllabs_email"))


if __name__ == "__main__":
    unittest.main()