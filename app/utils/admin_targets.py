#!/usr/bin/env python3
#
# app/utils/admin_targets.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Shared allow/deny policy for Caddy Admin API network targets.

Both the runtime-settings validator (which checks a user-supplied URL before it
is persisted) and the Admin API client (which pins a resolved IP before it
connects) must agree on which hosts and IP addresses are acceptable. Keeping the
policy in one place prevents the two from drifting apart, where settings could
store a value the client later refuses, or vice versa.
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address


_ALLOWED_ADMIN_HOSTS = frozenset({"localhost", "host.docker.internal", "caddy"})
_FORBIDDEN_ADMIN_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})
_FORBIDDEN_ADMIN_IPS = frozenset({ip_address("169.254.169.254")})

type ResolvedIPAddress = IPv4Address | IPv6Address


def normalize_resolved_ip(target_ip: ResolvedIPAddress) -> ResolvedIPAddress:
    """Collapse IPv4-mapped IPv6 addresses to their IPv4 form for policy checks."""
    return getattr(target_ip, "ipv4_mapped", None) or target_ip


def is_allowed_admin_ip(target_ip: ResolvedIPAddress) -> bool:
    """Return True when an already-resolved IP is an acceptable admin target."""
    normalized_ip = normalize_resolved_ip(target_ip)
    if normalized_ip in _FORBIDDEN_ADMIN_IPS:
        return False
    # Loopback is always a safe target. Check before is_reserved because
    # Python's ipaddress marks ::1 as both is_loopback and is_reserved.
    if normalized_ip.is_loopback:
        return True
    if (
        normalized_ip.is_link_local
        or normalized_ip.is_multicast
        or normalized_ip.is_unspecified
        or normalized_ip.is_reserved
    ):
        return False
    return normalized_ip.is_private


def validate_caddy_admin_host(host: str) -> None:
    """Apply the syntactic host policy, raising ValueError on a disallowed host.

    Hostnames must be on the allowlist; literal IP addresses must be loopback or
    private and must not be a blocked metadata address. DNS resolution is left to
    the caller (the client resolves and re-checks the concrete IP at connect
    time); this only covers what can be decided from the host string alone.
    """
    normalized_host = host.strip().lower()
    if not normalized_host:
        raise ValueError("Caddy admin host must not be empty.")
    if normalized_host in _FORBIDDEN_ADMIN_HOSTS:
        raise ValueError(f"Blocked Caddy admin target: {normalized_host!r}")

    try:
        target_ip = ip_address(normalized_host)
    except ValueError:
        if normalized_host not in _ALLOWED_ADMIN_HOSTS:
            raise ValueError(f"Caddy admin host is not allowed: {normalized_host!r}") from None
        return

    if not is_allowed_admin_ip(target_ip):
        raise ValueError(f"Caddy admin IP target is not allowed: {target_ip!s}")


# Backwards-compatible alias for the generic name used at the call sites.
validate_admin_host = validate_caddy_admin_host
