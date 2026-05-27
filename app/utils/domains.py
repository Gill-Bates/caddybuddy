#!/usr/bin/env python3
#
# app/utils/domains.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from ipaddress import ip_address
import re


_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.ASCII,
)
_DOMAIN_SPLIT_RE = re.compile(r"[\s,]+", re.ASCII)


def normalize_domain_name(
    value: str,
    *,
    required_message: str = "domain name cannot be empty",
    length_message: str = "domain name exceeds the DNS limit of 253 characters",
    invalid_message: str = "invalid domain name",
    ip_message: str | None = None,
) -> str:
    normalized = value.strip().lower().rstrip(".")
    if not normalized:
        raise ValueError(required_message)
    if len(normalized) > 253:
        raise ValueError(length_message)
    if _DOMAIN_RE.fullmatch(normalized) is None:
        raise ValueError(invalid_message)
    if ip_message is None:
        return normalized

    try:
        ip_address(normalized)
    except ValueError:
        return normalized
    raise ValueError(ip_message)


def split_domain_names(
    value: str,
    *,
    required_message: str = "domain name cannot be empty",
    length_message: str = "domain name exceeds the DNS limit of 253 characters",
    invalid_message: str = "invalid domain name",
    ip_message: str | None = None,
) -> tuple[str, ...]:
    tokens = [token for token in _DOMAIN_SPLIT_RE.split(value.strip()) if token]
    if not tokens:
        raise ValueError(required_message)

    seen: set[str] = set()
    normalized_domains: list[str] = []
    for token in tokens:
        normalized = normalize_domain_name(
            token,
            required_message=required_message,
            length_message=length_message,
            invalid_message=invalid_message,
            ip_message=ip_message,
        )
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_domains.append(normalized)

    return tuple(normalized_domains)


def normalize_domain_list(
    value: str,
    *,
    required_message: str = "domain name cannot be empty",
    length_message: str = "domain name exceeds the DNS limit of 253 characters",
    invalid_message: str = "invalid domain name",
    ip_message: str | None = None,
) -> str:
    return ", ".join(
        split_domain_names(
            value,
            required_message=required_message,
            length_message=length_message,
            invalid_message=invalid_message,
            ip_message=ip_message,
        )
    )