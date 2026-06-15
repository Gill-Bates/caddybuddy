#!/usr/bin/env python3
#
# tests/test_admin_targets.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from ipaddress import ip_address

from app.utils.admin_targets import (
    is_allowed_admin_ip,
    validate_admin_host,
    validate_caddy_admin_host,
)


class IsAllowedAdminIpTests(unittest.TestCase):
    def test_allows_loopback_and_private(self) -> None:
        self.assertTrue(is_allowed_admin_ip(ip_address("127.0.0.1")))
        self.assertTrue(is_allowed_admin_ip(ip_address("10.0.0.1")))
        self.assertTrue(is_allowed_admin_ip(ip_address("192.168.1.5")))

    def test_allows_ipv4_mapped_loopback(self) -> None:
        self.assertTrue(is_allowed_admin_ip(ip_address("::ffff:127.0.0.1")))

    def test_allows_ipv6_loopback(self) -> None:
        # ::1 is is_loopback=True; loopback is checked before is_reserved.
        self.assertTrue(is_allowed_admin_ip(ip_address("::1")))

    def test_blocks_metadata_ip_and_mapped_form(self) -> None:
        self.assertFalse(is_allowed_admin_ip(ip_address("169.254.169.254")))
        self.assertFalse(is_allowed_admin_ip(ip_address("::ffff:169.254.169.254")))

    def test_blocks_public_and_unspecified(self) -> None:
        self.assertFalse(is_allowed_admin_ip(ip_address("8.8.8.8")))
        self.assertFalse(is_allowed_admin_ip(ip_address("::")))
        self.assertFalse(is_allowed_admin_ip(ip_address("224.0.0.1")))  # multicast


class ValidateCaddyAdminHostTests(unittest.TestCase):
    def test_allows_allowlisted_hosts(self) -> None:
        for host in ("localhost", "host.docker.internal", "caddy", "127.0.0.1", "10.0.0.1"):
            validate_caddy_admin_host(host)

    def test_alias_matches_canonical(self) -> None:
        self.assertIs(validate_admin_host, validate_caddy_admin_host)

    def test_rejects_empty_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            validate_caddy_admin_host("   ")

    def test_rejects_metadata_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "Blocked Caddy admin target"):
            validate_caddy_admin_host("metadata.google.internal")

    def test_rejects_public_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "not allowed"):
            validate_caddy_admin_host("example.com")

    def test_rejects_public_ip(self) -> None:
        with self.assertRaisesRegex(ValueError, "not allowed"):
            validate_caddy_admin_host("8.8.8.8")


if __name__ == "__main__":
    unittest.main()
