#!/usr/bin/env python3
#
# tests/test_entities_hardening.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest

from app.models.entities import (
    CaddyConfigVersion,
    CaddySyncEvent,
    CaddyfileSnapshot,
    Site,
    SslLabsScan,
    SslLabsTarget,
    User,
)


class EntityHardeningTests(unittest.TestCase):
    def test_site_accepts_valid_domain_and_upstream_url(self) -> None:
        site = Site(domain="Example.com.", upstream_url="http://backend.internal:8080", enabled=True)

        self.assertEqual(site.domain, "example.com")
        self.assertEqual(site.upstream_url, "http://backend.internal:8080")
        self.assertTrue(site.enabled)

    def test_site_normalizes_multiple_domains_and_deduplicates(self) -> None:
        site = Site(
            domain=" Example.com,\twww.example.com example.com ",
            upstream_url="http://backend.internal:8080",
            enabled=True,
        )

        self.assertEqual(site.domain, "example.com, www.example.com")

    def test_site_rejects_invalid_domain_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid domain name"):
            Site(domain="https://example.com", upstream_url="http://backend.internal:8080", enabled=True)

    def test_site_rejects_invalid_upstream_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "upstream_url must use http or https"):
            Site(domain="example.com", upstream_url="backend.internal:8080", enabled=True)

    def test_site_rejects_upstream_url_with_newlines(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain newlines"):
            Site(
                domain="example.com",
                upstream_url="http://backend.internal:8080\nheader_down x y",
                enabled=True,
            )

    def test_user_normalizes_role_and_email(self) -> None:
        user = User(
            username="admin",
            email=" Admin@Example.com ",
            password_hash="hash",
            role=" ADMIN ",
            is_active=True,
        )

        self.assertEqual(user.role, "admin")
        self.assertEqual(user.email, "admin@example.com")
        self.assertTrue(user.is_admin)

    def test_user_rejects_invalid_role(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid user role"):
            User(username="admin", email=None, password_hash="hash", role="root", is_active=True)

    def test_ssllabs_target_rejects_url_like_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "hostname|public hostname"):
            SslLabsTarget(site_id=1, host="https://example.com")

    def test_ssllabs_scan_rejects_negative_endpoint_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "endpoint_count must be non-negative"):
            SslLabsScan(target_id=1, site_id=1, host="example.com", endpoint_count=-1)

    def test_snapshot_rejects_invalid_sha256(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid sha256"):
            CaddyfileSnapshot(content="x", sha256="not-a-hash", source_path="/tmp/Caddyfile")

    def test_config_version_normalizes_valid_sha256(self) -> None:
        version = CaddyConfigVersion(rendered_config="{}", sha256="A" * 64)

        self.assertEqual(version.sha256, "a" * 64)

    def test_sync_event_rejects_unknown_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid caddy sync event status"):
            CaddySyncEvent(status="kaputt")

    def test_sync_event_rejects_invalid_config_sha256(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid sha256"):
            CaddySyncEvent(status="synced", config_sha256="bad")


if __name__ == "__main__":
    unittest.main()