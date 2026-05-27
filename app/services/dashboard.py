#!/usr/bin/env python3
#
# app/services/dashboard.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import logging
import math
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.repositories.sites import site_repository
from app.services.caddy import CaddyAdminClient, CaddyServiceError
from app.utils.domains import split_domain_names


logger = logging.getLogger(__name__)
_CERT_FETCH_TIMEOUT = 5.0
_CERT_FETCH_CONCURRENCY = 10
_CERT_CACHE_TTL_SECONDS = 600  # 10 minutes

# Simple in-memory cache for certificate info
_cert_cache: dict[str, tuple[datetime, CertificateInfo]] = {}
_cert_cache_lock = asyncio.Lock()


@dataclass(slots=True, frozen=True)
class DashboardMetrics:
    domain_count: int
    enabled_domain_count: int
    valid_certificate_count: int
    expired_certificate_count: int
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
    """SSL certificate information for a domain."""
    exists: bool
    valid: bool = False
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    days_remaining: int | None = None


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


def _fetch_remote_certificate_sync(domain: str) -> CertificateInfo:
    """Fetch certificate info by connecting to the domain over HTTPS.
    
    Uses CERT_NONE to read certificates even if expired/invalid.
    Validity is determined by notAfter date, not TLS verification.
    """
    try:
        # Disable verification to read expired/self-signed certs
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection((domain, 443), timeout=_CERT_FETCH_TIMEOUT) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert(binary_form=True)
                if not cert:
                    return CertificateInfo(exists=False)

                # Decode binary DER certificate
                from cryptography import x509
                from cryptography.hazmat.backends import default_backend
                
                x509_cert = x509.load_der_x509_certificate(cert, default_backend())
                issued_at = x509_cert.not_valid_before_utc
                expires_at = x509_cert.not_valid_after_utc

                now = datetime.now(UTC)
                info = _certificate_info_from_dates(issued_at, expires_at, now)
                return info if info else CertificateInfo(exists=False)

    except (OSError, ssl.SSLError, socket.timeout, socket.gaierror) as exc:
        logger.debug("Failed to fetch certificate for %s: %s", domain, exc)
        return CertificateInfo(exists=False)
    except Exception as exc:
        logger.debug("Error decoding certificate for %s: %s", domain, exc)
        return CertificateInfo(exists=False)


async def _fetch_remote_certificate(domain: str) -> tuple[str, CertificateInfo]:
    """Async wrapper for remote certificate fetching."""
    info = await asyncio.to_thread(_fetch_remote_certificate_sync, domain)
    return domain, info


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
    unique_domains = sorted({d.lower().strip() for d in domains})
    
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
                logger.debug("Certificate fetch failed for %s: %s", domain, result)
                info = CertificateInfo(exists=False)
            else:
                info = result[1]
            
            cert_info[domain] = info
            _cert_cache[domain] = (now, info)

    return cert_info


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


async def _get_caddy_service_metrics() -> HostServiceMetrics:
    """Read Caddy runtime status and version from the Admin API."""
    settings = get_settings()
    if not settings.caddy_admin_url:
        return HostServiceMetrics(status="Unknown", uptime="Unavailable", version="Unavailable")

    try:
        async with CaddyAdminClient(
            settings.caddy_admin_url,
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
    try:
        certificate_data = ssl._ssl._test_decode_cert(str(certificate_path))
    except Exception:
        return None

    not_after = certificate_data.get("notAfter")
    if not isinstance(not_after, str) or not not_after.strip():
        return None

    try:
        return datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), UTC)
    except Exception:
        return None


def _decode_certificate_details(certificate_path: Path) -> tuple[datetime | None, datetime | None]:
    """Decode certificate issued and expiry dates."""
    try:
        certificate_data = ssl._ssl._test_decode_cert(str(certificate_path))
    except Exception:
        return None, None

    issued_at = None
    expires_at = None

    not_before = certificate_data.get("notBefore")
    if isinstance(not_before, str) and not_before.strip():
        try:
            issued_at = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_before), UTC)
        except Exception:
            pass

    not_after = certificate_data.get("notAfter")
    if isinstance(not_after, str) and not_after.strip():
        try:
            expires_at = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), UTC)
        except Exception:
            pass

    return issued_at, expires_at


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


async def get_caddy_status() -> HostServiceMetrics:
    """Get current Caddy service status for API endpoint."""
    return await _get_caddy_service_metrics()


async def get_dashboard_metrics(session: AsyncSession) -> DashboardMetrics:
    sites = await site_repository.list_all(session)

    # Extract and deduplicate all domains from sites
    all_domains = sorted({
        domain_name.lower().strip()
        for site in sites
        for domain_name in split_domain_names(site.domain)
    })

    # Extract domains from enabled sites only
    enabled_domains = sorted({
        domain_name.lower().strip()
        for site in sites
        if site.enabled
        for domain_name in split_domain_names(site.domain)
    })

    domain_count = len(all_domains)
    enabled_domain_count = len(enabled_domains)

    # Fetch certificate info by connecting to domains over HTTPS (cached)
    valid_certificate_count = 0
    expired_certificate_count = 0

    if all_domains:
        cert_info = await get_certificate_info_for_domains_remote(all_domains)
        for info in cert_info.values():
            if info.exists:
                if info.valid:
                    valid_certificate_count += 1
                else:
                    expired_certificate_count += 1

    host_service_metrics = await _get_caddy_service_metrics()

    return DashboardMetrics(
        domain_count=domain_count,
        enabled_domain_count=enabled_domain_count,
        valid_certificate_count=valid_certificate_count,
        expired_certificate_count=expired_certificate_count,
        caddy_service_status=host_service_metrics.status,
        caddy_service_uptime=host_service_metrics.uptime,
        caddy_version=host_service_metrics.version,
    )