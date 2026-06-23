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
        "CADDYBUDDY_SECRET_KEY": "StrongSecretKey-1234567890abcdef",
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
                    caddybuddy_SECRET_KEY="StrongSecretKey-1234567890abcdef",
                )

    def test_caddy_api_url_uses_cb_env_only(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CB_CADDY_API_URL": "http://host.docker.internal:2019",
                "CB_CADDYFILE_PATH": "/etc/caddy/Caddyfile",
            },
            clear=True,
        ):
            settings = Settings(
                CB_SECRET_KEY="StrongSecretKey-1234567890abcdef",
            )

        self.assertEqual(settings.caddy_admin_url, DEFAULT_CADDY_ADMIN_URL)
        self.assertEqual(settings.caddy_api_url, "http://host.docker.internal:2019")
        self.assertEqual(settings.mounted_caddyfile_path, DEFAULT_CADDYFILE_PATH)

    def test_legacy_caddybuddy_caddy_api_url_alias_is_ignored(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CADDYBUDDY_CADDY_API_URL": "http://host.docker.internal:2019",
            },
            clear=True,
        ):
            settings = Settings(
                CB_SECRET_KEY="StrongSecretKey-1234567890abcdef",
            )

        self.assertEqual(settings.caddy_api_url, DEFAULT_CADDY_ADMIN_URL)

    def test_mounted_caddyfile_path_requires_caddyfile_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "must point to a file named 'Caddyfile'"):
            Settings(
                **_settings_kwargs(
                    mounted_caddyfile_path="/etc/caddy/custom.conf",
                )
            )

    def test_default_caddy_baseline_uses_central_runtime_log(self) -> None:
        settings = Settings(**_settings_kwargs())

        self.assertIn("output file /var/log/caddy/runtime.json", settings.caddy_baseline_caddyfile)
        self.assertIn("roll_size 10MiB", settings.caddy_baseline_caddyfile)
        self.assertNotIn("(default_log)", settings.caddy_baseline_caddyfile)

    def test_ssllabs_url_is_normalized_and_email_env_is_ignored(self) -> None:
        settings = Settings(
            **_settings_kwargs(
                CADDYBUDDY_SSLLABS_EMAIL=" security@example.com ",
                CADDYBUDDY_SSLLABS_API_BASE_URL="https://api.ssllabs.com/api/v4/",
            )
        )

        self.assertEqual(settings.ssllabs_api_base_url, "https://api.ssllabs.com/api/v4")
        self.assertFalse(hasattr(settings, "ssllabs_email"))

    def test_session_cookie_name_is_stripped_and_validated(self) -> None:
        settings = Settings(**_settings_kwargs(session_cookie_name=" caddybuddy.session "))

        self.assertEqual(settings.session_cookie_name, "caddybuddy.session")

    def test_session_cookie_name_rejects_invalid_characters(self) -> None:
        with self.assertRaisesRegex(ValueError, "session_cookie_name may contain only"):
            Settings(**_settings_kwargs(session_cookie_name="caddybuddy;session"))

    def test_session_cookie_samesite_none_requires_https_only(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires session_https_only=true"):
            Settings(
                **_settings_kwargs(
                    session_cookie_samesite="none",
                    session_https_only=False,
                )
            )

    def test_ssllabs_http_url_is_allowed_for_localhost_only(self) -> None:
        settings = Settings(**_settings_kwargs(ssllabs_api_base_url="http://localhost:8080/api/v4"))

        self.assertEqual(settings.ssllabs_api_base_url, "http://localhost:8080/api/v4")

    def test_ssllabs_http_url_rejects_non_localhost_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "must use https unless it targets localhost"):
            Settings(**_settings_kwargs(ssllabs_api_base_url="http://api.ssllabs.com/api/v4"))


if __name__ == "__main__":
    unittest.main()
