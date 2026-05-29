#!/usr/bin/env python3
#
# app/services/dashboard.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import logging
import math
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from typing import Protocol

from cryptography import x509

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.repositories.sites import site_repository
from app.services.caddy import CaddyAdminClient, CaddyServiceError
from app.services.runtime_settings import get_caddy_config
from app.utils.domains import split_domain_names
from app.utils.ssllabs import validate_ssllabs_host


logger = logging.getLogger(__name__)
_CERT_FETCH_TIMEOUT = 5.0
_CERT_FETCH_CONCURRENCY = 10
_CERT_CACHE_TTL_SECONDS = 600  # 10 minutes
_MAX_CERT_CACHE_ENTRIES = 1000
_EXPIRING_SOON_DAYS = 7
type ResolvedIPAddress = IPv4Address | IPv6Address

# Simple in-memory cache for certificate info
_cert_cache: dict[str, tuple[datetime, CertificateInfo]] = {}
_cert_cache_lock = asyncio.Lock()


async def invalidate_certificate_cache(domain: str) -> None:
    """Remove a domain from the certificate cache."""
    normalized = domain.lower().strip()
    async with _cert_cache_lock:
        _cert_cache.pop(normalized, None)


def _prune_cert_cache() -> None:
    if len(_cert_cache) <= _MAX_CERT_CACHE_ENTRIES:
        return

    overflow = len(_cert_cache) - _MAX_CERT_CACHE_ENTRIES
    for domain, _value in sorted(_cert_cache.items(), key=lambda item: item[1][0])[:overflow]:
        _cert_cache.pop(domain, None)


@dataclass(slots=True, frozen=True)
class DashboardMetrics:
    domain_count: int
    enabled_domain_count: int
    valid_certificate_count: int | None
    expired_certificate_count: int | None
    expiring_soon_certificate_count: int | None
    caddy_service_status: str
    caddy_service_uptime: str
    caddy_version: str


@dataclass(slots=True, frozen=True)
class HostServiceMetrics:
    status: str
    uptime: str
    version: str


@dataclass(slots=True, frozen=True)
class CertificateInfo:
    """Time-based SSL certificate information for a domain.

    `valid` reflects only the certificate validity window. Trust chain and
    hostname matching are not evaluated here.
    """
    exists: bool
    valid: bool = False
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    days_remaining: int | None = None
    error_message: str | None = None


class SiteLike(Protocol):
    domain: str
    enabled: bool


def _build_certificate_fetch_error_message(exc: Exception) -> str:
    raw_message = str(exc).strip()
    lower_message = raw_message.lower()

    if isinstance(exc, ssl.SSLError):
        if "tlsv1 alert internal error" in lower_message or "tls alert internal error" in lower_message:
            return "TLS handshake failed: the remote server aborted the connection with an internal TLS error."
        if "handshake failure" in lower_message:
            return "TLS handshake failed: the remote server rejected the TLS handshake."
        if "wrong version number" in lower_message:
            return "TLS handshake failed: the server did not accept the negotiated TLS version."
        if raw_message:
            return f"TLS handshake failed: {raw_message}"
        return "TLS handshake failed while reading the certificate."

    if isinstance(exc, socket.gaierror):
        return "DNS lookup failed for this domain."

    if isinstance(exc, socket.timeout | TimeoutError):
        return "Connection timed out while reading the certificate."

    if isinstance(exc, ConnectionRefusedError):
        return "Connection to port 443 was refused by the remote server."

    if isinstance(exc, OSError):
        if raw_message:
            return f"Connection failed: {raw_message}"
        return "Connection failed while reading the certificate."

    if raw_message:
        return f"Certificate check failed: {raw_message}"
    return "Certificate check failed unexpectedly."


def _certificate_info_from_dates(
    issued_at: datetime | None,
    expires_at: datetime | None,
    now: datetime,
) -> CertificateInfo | None:
    if expires_at is None:
        return None

    seconds_remaining = int((expires_at - now).total_seconds())
    return CertificateInfo(
        exists=True,
        valid=seconds_remaining > 0,
        issued_at=issued_at,
        expires_at=expires_at,
        days_remaining=math.ceil(seconds_remaining / 86400) if seconds_remaining > 0 else 0,
    )


def _normalize_resolved_ip(target_ip: ResolvedIPAddress) -> ResolvedIPAddress:
    return getattr(target_ip, "ipv4_mapped", None) or target_ip


def _is_public_certificate_ip(target_ip: ResolvedIPAddress) -> bool:
    normalized_ip = _normalize_resolved_ip(target_ip)
    return not (
        normalized_ip.is_private
        or normalized_ip.is_loopback
        or normalized_ip.is_link_local
        or normalized_ip.is_multicast
        or normalized_ip.is_reserved
        or normalized_ip.is_unspecified
    )


def _validate_public_certificate_target(domain: str) -> tuple[str, str]:
    normalized_domain = validate_ssllabs_host(domain)

    address_info = socket.getaddrinfo(
        normalized_domain,
        443,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )

    pinned_ip: str | None = None
    for *_prefix, sockaddr in address_info:
        if not sockaddr or not sockaddr[0]:
            continue
        target_ip = ip_address(sockaddr[0])
        if not _is_public_certificate_ip(target_ip):
            raise ValueError("Certificate checks require a public hostname.")
        if pinned_ip is None:
            pinned_ip = sockaddr[0]

    if pinned_ip is None:
        raise ValueError("Certificate target did not resolve.")
    return normalized_domain, pinned_ip


def _load_x509_certificate_bytes(certificate_bytes: bytes) -> x509.Certificate | None:
    try:
        return x509.load_pem_x509_certificate(certificate_bytes)
    except ValueError:
        try:
            return x509.load_der_x509_certificate(certificate_bytes)
        except ValueError:
            return None


def _load_x509_certificate_from_path(certificate_path: Path) -> x509.Certificate | None:
    try:
        certificate_bytes = certificate_path.read_bytes()
    except OSError:
        return None
    return _load_x509_certificate_bytes(certificate_bytes)


def _fetch_remote_certificate_sync(domain: str) -> CertificateInfo:
    """Fetch certificate info by connecting to the domain over HTTPS.
    
    Uses CERT_NONE to read certificates even if expired/invalid.
    Validity is determined by notAfter date, not TLS verification.
    """
    try:
        validated_domain, pinned_ip = _validate_public_certificate_target(domain)
    except ValueError as exc:
        logger.warning("Blocked certificate check for %s: %s", domain, exc)
        return CertificateInfo(exists=False, error_message=str(exc))
    except socket.gaierror as exc:
        return CertificateInfo(exists=False, error_message=_build_certificate_fetch_error_message(exc))

    try:
        # Disable verification to read expired/self-signed certs
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection((pinned_ip, 443), timeout=_CERT_FETCH_TIMEOUT) as sock:
            with context.wrap_socket(sock, server_hostname=validated_domain) as ssock:
                cert = ssock.getpeercert(binary_form=True)
                if not cert:
                    return CertificateInfo(exists=False)

                # Decode binary DER certificate
                x509_cert = x509.load_der_x509_certificate(cert)
                issued_at = x509_cert.not_valid_before_utc
                expires_at = x509_cert.not_valid_after_utc

                now = datetime.now(UTC)
                info = _certificate_info_from_dates(issued_at, expires_at, now)
                return info if info else CertificateInfo(exists=False)

    except (OSError, ssl.SSLError, socket.timeout, socket.gaierror) as exc:
        logger.error("Failed to fetch certificate for %s: %s", domain, exc)
        return CertificateInfo(exists=False, error_message=_build_certificate_fetch_error_message(exc))
    except Exception as exc:
        logger.error("Error decoding certificate for %s: %s", domain, exc)
        return CertificateInfo(exists=False, error_message=_build_certificate_fetch_error_message(exc))


async def _fetch_remote_certificate(domain: str) -> tuple[str, CertificateInfo]:
    """Async wrapper for remote certificate fetching."""
    info = await asyncio.to_thread(_fetch_remote_certificate_sync, domain)
    return domain, info


def _normalize_domains(domains: list[str]) -> list[str]:
    return sorted({domain.lower().strip() for domain in domains if domain and domain.strip()})


def _is_cache_valid(cached_at: datetime) -> bool:
    """Check if cached entry is still valid."""
    return (datetime.now(UTC) - cached_at).total_seconds() < _CERT_CACHE_TTL_SECONDS


async def get_certificate_info_for_domains_remote(
    domains: list[str],
) -> dict[str, CertificateInfo]:
    """Fetch certificate info for multiple domains with caching.
    
    Results are cached for 10 minutes to avoid excessive network requests.
    """
    if not domains:
        return {}

    # Deduplicate and normalize domains
    unique_domains = _normalize_domains(domains)
    
    now = datetime.now(UTC)
    cert_info: dict[str, CertificateInfo] = {}
    domains_to_fetch: list[str] = []

    # Check cache first
    async with _cert_cache_lock:
        for domain in unique_domains:
            if domain in _cert_cache:
                cached_at, cached_info = _cert_cache[domain]
                if _is_cache_valid(cached_at):
                    cert_info[domain] = cached_info
                    continue
            domains_to_fetch.append(domain)

    if not domains_to_fetch:
        logger.debug("All %d domains served from cache", len(unique_domains))
        return cert_info

    logger.debug("Fetching certificates for %d domains (%d from cache)", 
                 len(domains_to_fetch), len(cert_info))

    # Fetch uncached domains
    semaphore = asyncio.Semaphore(_CERT_FETCH_CONCURRENCY)

    async def fetch_with_limit(domain: str) -> tuple[str, CertificateInfo]:
        async with semaphore:
            return await _fetch_remote_certificate(domain)

    tasks = [fetch_with_limit(d) for d in domains_to_fetch]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results and update cache
    async with _cert_cache_lock:
        for i, result in enumerate(results):
            domain = domains_to_fetch[i]
            if isinstance(result, Exception):
                logger.error("Certificate fetch failed for %s: %s", domain, result)
                info = CertificateInfo(exists=False, error_message=_build_certificate_fetch_error_message(result))
            else:
                info = result[1]
            
            cert_info[domain] = info
            _cert_cache[domain] = (now, info)
            _prune_cert_cache()

    return cert_info


async def get_cached_certificate_info_for_domains(
    domains: list[str],
    *,
    allow_stale: bool = True,
) -> dict[str, CertificateInfo]:
    """Return cached certificate info without triggering remote network fetches."""
    unique_domains = _normalize_domains(domains)
    if not unique_domains:
        return {}

    cached_results: dict[str, CertificateInfo] = {}
    async with _cert_cache_lock:
        for domain in unique_domains:
            cached = _cert_cache.get(domain)
            if cached is None:
                continue
            cached_at, info = cached
            if allow_stale or _is_cache_valid(cached_at):
                cached_results[domain] = info
    return cached_results


def _build_certificate_index(certificates_path: Path | None) -> dict[str, CertificateInfo]:
    """Index certificates by domain directory for efficient dashboard lookups."""
    if certificates_path is None:
        return {}

    try:
        if not certificates_path.is_dir():
            return {}
        cert_files = list(certificates_path.rglob("*.crt"))
    except (OSError, PermissionError):
        return {}

    now = datetime.now(UTC)
    certificate_index: dict[str, CertificateInfo] = {}
    for cert_path in cert_files:
        try:
            if not cert_path.is_file():
                continue
            domain_name = cert_path.parent.name.lower().strip()
            issued_at, expires_at = _decode_certificate_details(cert_path)
        except (OSError, PermissionError):
            continue

        certificate_info = _certificate_info_from_dates(issued_at, expires_at, now)
        if certificate_info is None:
            continue
        certificate_index[domain_name] = certificate_info

    return certificate_index


async def _get_caddy_service_metrics(session: AsyncSession) -> HostServiceMetrics:
    """Read Caddy runtime status and version from the Admin API."""
    config = await get_caddy_config(session)
    settings = get_settings()
    if not config.admin_url:
        return HostServiceMetrics(status="Unknown", uptime="Unavailable", version="Unavailable")

    try:
        async with CaddyAdminClient(
            config.admin_url,
            timeout_seconds=min(settings.caddy_admin_timeout_seconds, 2.0),
        ) as client:
            if not await client.health():
                return HostServiceMetrics(status="Unknown", uptime="Unavailable", version="Unavailable")
            version = await client.get_version() or "Unavailable"
            return HostServiceMetrics(
                status="Running",
                uptime="Unavailable",
                version=version,
            )
    except (CaddyServiceError, ValueError, OSError) as exc:
        logger.debug("Unable to read Caddy dashboard metrics from Admin API: %s", exc)

    return HostServiceMetrics(status="Unknown", uptime="Unavailable", version="Unavailable")


def _decode_certificate_expiry(certificate_path: Path) -> datetime | None:
    certificate = _load_x509_certificate_from_path(certificate_path)
    if certificate is None:
        return None
    return certificate.not_valid_after_utc


def _decode_certificate_details(certificate_path: Path) -> tuple[datetime | None, datetime | None]:
    """Decode certificate issued and expiry dates."""
    certificate = _load_x509_certificate_from_path(certificate_path)
    if certificate is None:
        return None, None
    return certificate.not_valid_before_utc, certificate.not_valid_after_utc


def _find_certificate_for_domain(certificates_path: Path | None, domain: str) -> CertificateInfo:
    """Find certificate info for a specific domain."""
    certificate_index = _build_certificate_index(certificates_path)
    return certificate_index.get(domain.lower().strip(), CertificateInfo(exists=False))


async def get_certificate_info_for_domains(domains: list[str]) -> dict[str, CertificateInfo]:
    """Get certificate info for multiple domains by fetching from remote hosts."""
    return await get_certificate_info_for_domains_remote(domains)


def _scan_certificate_counts(certificates_path: Path | None) -> tuple[int, int]:
    if certificates_path is None:
        logger.debug("Certificate path is None, returning 0 counts")
        return 0, 0

    try:
        if not certificates_path.is_dir():
            logger.debug("Certificate path %s is not a directory", certificates_path)
            return 0, 0
    except (OSError, PermissionError) as exc:
        logger.debug("Cannot access certificate path %s: %s", certificates_path, exc)
        return 0, 0

    valid_count = 0
    expired_count = 0
    now = datetime.now(UTC)

    try:
        cert_files = list(certificates_path.rglob("*.crt"))
        logger.debug("Found %d .crt files in %s", len(cert_files), certificates_path)
    except (OSError, PermissionError) as exc:
        logger.debug("Cannot scan certificate path %s: %s", certificates_path, exc)
        return 0, 0

    for certificate_path in cert_files:
        try:
            if not certificate_path.is_file():
                continue
            expires_at = _decode_certificate_expiry(certificate_path)
        except (OSError, PermissionError):
            continue
        if expires_at is None:
            logger.debug("Could not decode expiry from %s", certificate_path)
            continue
        if expires_at < now:
            expired_count += 1
        else:
            valid_count += 1

    logger.debug("Certificate scan result: %d valid, %d expired", valid_count, expired_count)
    return valid_count, expired_count


async def get_caddy_status(session: AsyncSession) -> HostServiceMetrics:
    """Get current Caddy service status for API endpoint."""
    return await _get_caddy_service_metrics(session)


def _collect_domain_counts(sites: Iterable[SiteLike]) -> tuple[int, int]:
    all_domains = sorted({
        domain_name.lower().strip()
        for site in sites
        for domain_name in split_domain_names(site.domain)
    })
    enabled_domains = sorted({
        domain_name.lower().strip()
        for site in sites
        if site.enabled
        for domain_name in split_domain_names(site.domain)
    })
    return len(all_domains), len(enabled_domains)


async def get_dashboard_shell_metrics(session: AsyncSession) -> DashboardMetrics:
    """Return cheap dashboard metrics for the initial page render.

    Expensive network-bound checks are intentionally deferred to the JSON API
    so the dashboard can render immediately after login.
    """
    sites = await site_repository.list_all(session)
    domain_count, enabled_domain_count = _collect_domain_counts(sites)

    return DashboardMetrics(
        domain_count=domain_count,
        enabled_domain_count=enabled_domain_count,
        valid_certificate_count=None,
        expired_certificate_count=None,
        expiring_soon_certificate_count=None,
        caddy_service_status="Unknown",
        caddy_service_uptime="Unavailable",
        caddy_version="Unavailable",
    )


async def get_dashboard_metrics(session: AsyncSession) -> DashboardMetrics:
    sites = await site_repository.list_all(session)
    domain_count, enabled_domain_count = _collect_domain_counts(sites)
    all_domains = sorted({
        domain_name.lower().strip()
        for site in sites
        for domain_name in split_domain_names(site.domain)
    })

    # Fetch certificate info by connecting to domains over HTTPS (cached)
    valid_certificate_count = 0
    expired_certificate_count = 0
    expiring_soon_certificate_count = 0

    if all_domains:
        cert_info = await get_certificate_info_for_domains_remote(all_domains)
        for info in cert_info.values():
            if info.exists:
                if info.valid:
                    valid_certificate_count += 1
                    if info.days_remaining is not None and info.days_remaining <= _EXPIRING_SOON_DAYS:
                        expiring_soon_certificate_count += 1
                else:
                    expired_certificate_count += 1

    host_service_metrics = await _get_caddy_service_metrics(session)

    return DashboardMetrics(
        domain_count=domain_count,
        enabled_domain_count=enabled_domain_count,
        valid_certificate_count=valid_certificate_count,
        expired_certificate_count=expired_certificate_count,
        expiring_soon_certificate_count=expiring_soon_certificate_count,
        caddy_service_status=host_service_metrics.status,
        caddy_service_uptime=host_service_metrics.uptime,
        caddy_version=host_service_metrics.version,
    )