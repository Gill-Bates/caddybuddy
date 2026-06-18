#!/usr/bin/env python3
#
# app/services/dashboard.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import logging
import socket
import ssl
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
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
from app.services.certificates import (
    CertificateInfo,
    normalize_domains,
    certificate_info_from_dates,
    scan_certificate_storage,
    find_certificate_for_domain,
    certificate_coverage_for_domain,
)


logger = logging.getLogger(__name__)
_CERT_FETCH_TIMEOUT = 5.0
_CERT_FETCH_CONCURRENCY = 10
_CERT_CACHE_TTL_SECONDS = 600  # 10 minutes
_MAX_CERT_CACHE_ENTRIES = 1000
_EXPIRING_SOON_DAYS = 7
_PENDING_ISSUANCE_WINDOW = timedelta(minutes=30)
type ResolvedIPAddress = IPv4Address | IPv6Address

_cert_cache: dict[str, tuple[datetime, CertificateInfo]] = {}
_cert_cache_generations: dict[str, int] = {}
_cert_cache_lock = asyncio.Lock()
_CERT_FETCH_SEMAPHORE = asyncio.Semaphore(_CERT_FETCH_CONCURRENCY)
_cert_fetch_tasks: dict[str, asyncio.Task[tuple[str, CertificateInfo]]] = {}


async def invalidate_certificate_cache(domain: str) -> None:
    normalized_domains = normalize_domains([domain])
    if not normalized_domains:
        return
    normalized = normalized_domains[0]
    async with _cert_cache_lock:
        _cert_cache_generations[normalized] = _cert_cache_generations.get(normalized, 0) + 1
        _cert_cache.pop(normalized, None)


def _prune_cert_cache() -> None:
    if len(_cert_cache) <= _MAX_CERT_CACHE_ENTRIES:
        return

    overflow = len(_cert_cache) - _MAX_CERT_CACHE_ENTRIES
    for domain, _value in sorted(_cert_cache.items(), key=lambda item: item[1][0])[:overflow]:
        _cert_cache.pop(domain, None)


def get_local_certificate_info_for_domains(domains: list[str]) -> tuple[dict[str, CertificateInfo], bool]:
    """Return certificate info from local Caddy storage when accessible.

    Returns ``(results, storage_error)`` where *storage_error* is ``True``
    when the certificate store could not be read (permission denied, I/O
    error).  An unreadable store must not be treated as an empty store.
    """
    unique_domains = normalize_domains(domains)
    if not unique_domains:
        return {}, False

    certificates_path = get_settings().caddy_certificates_path
    certs, storage_error = scan_certificate_storage(certificates_path)
    certificate_index: dict[str, CertificateInfo] = {}
    for domain in unique_domains:
        if (info := find_certificate_for_domain(certs, domain)) is not None:
            certificate_index[domain] = info
    return certificate_index, storage_error


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


def _certificate_fetch_error_info(
    *,
    status: str,
    error_message: str,
    checked_at: datetime | None = None,
    source: str = "remote",
) -> CertificateInfo:
    return CertificateInfo(
        exists=False,
        status=status,
        source=source,
        error_message=error_message,
        checked_at=checked_at or datetime.now(UTC),
    )





def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_pending_certificate_request(
    *,
    enabled: bool,
    updated_at: datetime | None,
    now: datetime,
) -> bool:
    if not enabled or updated_at is None:
        return False
    age = now - _as_utc(updated_at)
    return timedelta(0) <= age <= _PENDING_ISSUANCE_WINDOW


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


async def _resolve_public_certificate_target(domain: str) -> tuple[str, str]:
    normalized_domain = validate_ssllabs_host(domain)
    loop = asyncio.get_running_loop()
    try:
        address_info = await asyncio.wait_for(
            loop.getaddrinfo(
                normalized_domain,
                443,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            ),
            timeout=_CERT_FETCH_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        raise TimeoutError("DNS lookup timed out for this domain.") from exc

    pinned_ip: str | None = None
    for *_prefix, sockaddr in address_info:
        if not sockaddr or not sockaddr[0]:
            continue
        target_ip = ip_address(sockaddr[0])
        if not _is_public_certificate_ip(target_ip):
            raise ValueError("Certificate checks require a public hostname.")
        if pinned_ip is None:
            pinned_ip = str(target_ip)

    if pinned_ip is None:
        raise ValueError("Certificate target did not resolve.")
    return normalized_domain, pinned_ip





def _read_peer_certificate_bytes(
    validated_domain: str,
    pinned_ip: str,
    *,
    verify: bool,
) -> bytes | None:
    context = ssl.create_default_context()
    context.check_hostname = verify
    context.verify_mode = ssl.CERT_REQUIRED if verify else ssl.CERT_NONE
    with socket.create_connection((pinned_ip, 443), timeout=_CERT_FETCH_TIMEOUT) as sock:
        with context.wrap_socket(sock, server_hostname=validated_domain) as ssock:
            return ssock.getpeercert(binary_form=True) or None


def _fetch_remote_certificate_sync_for_target(validated_domain: str, pinned_ip: str) -> CertificateInfo:
    try:
        verification_error: str | None = None
        try:
            cert = _read_peer_certificate_bytes(validated_domain, pinned_ip, verify=True)
        except ssl.SSLCertVerificationError as exc:
            verification_error = exc.verify_message or str(exc)
            cert = _read_peer_certificate_bytes(validated_domain, pinned_ip, verify=False)

        if not cert:
            return _certificate_fetch_error_info(
                status="error",
                error_message="No certificate was presented by the remote server.",
                checked_at=datetime.now(UTC),
            )

        x509_cert = x509.load_der_x509_certificate(cert)
        issued_at = x509_cert.not_valid_before_utc
        expires_at = x509_cert.not_valid_after_utc

        now = datetime.now(UTC)
        covers, match_type, covering_name, is_wildcard = certificate_coverage_for_domain(x509_cert, validated_domain)
        if not covers:
            return _certificate_fetch_error_info(
                status="error",
                error_message="served certificate does not cover this host",
                checked_at=now,
            )

        info = certificate_info_from_dates(
            issued_at,
            expires_at,
            now,
            source="remote",
            match_type=match_type,
            is_wildcard=is_wildcard,
            covering_name=covering_name,
            checked_at=now,
        )
        if info is None:
            return _certificate_fetch_error_info(
                status="error",
                error_message="Certificate validity dates could not be read.",
                checked_at=now,
            )

        if verification_error is not None:
            return replace(
                info,
                valid=False,
                status="error",
                error_message=f"TLS certificate verification failed: {verification_error}",
            )

        return info

    except (OSError, ssl.SSLError, socket.timeout) as exc:
        logger.warning("Failed to fetch certificate for %s: %s", validated_domain, exc)
        return _certificate_fetch_error_info(
            status="error",
            error_message=_build_certificate_fetch_error_message(exc),
        )
    except Exception as exc:
        logger.exception("Unexpected error decoding certificate for %s", validated_domain)
        return _certificate_fetch_error_info(
            status="error",
            error_message=_build_certificate_fetch_error_message(exc),
        )


async def _fetch_remote_certificate(domain: str) -> tuple[str, CertificateInfo]:
    """Async wrapper for remote certificate fetching."""
    async with _CERT_FETCH_SEMAPHORE:
        try:
            validated_domain, pinned_ip = await _resolve_public_certificate_target(domain)
        except ValueError as exc:
            logger.warning("Blocked certificate check for %s: %s", domain, exc)
            error_message = str(exc)
            status = "remote_check_unavailable" if "public hostname" in error_message.lower() else "error"
            return domain, CertificateInfo(
                exists=False,
                status=status,
                error_message=error_message,
                checked_at=datetime.now(UTC),
            )
        except socket.gaierror as exc:
            logger.warning("DNS lookup failed for certificate check %s: %s", domain, exc)
            return domain, CertificateInfo(
                exists=False,
                status="remote_check_unavailable",
                error_message=_build_certificate_fetch_error_message(exc),
                checked_at=datetime.now(UTC),
            )
        except TimeoutError as exc:
            logger.warning("Certificate check timed out for %s: %s", domain, exc)
            return domain, CertificateInfo(
                exists=False,
                status="error",
                error_message=_build_certificate_fetch_error_message(exc),
                checked_at=datetime.now(UTC),
            )
        info = await asyncio.to_thread(
            _fetch_remote_certificate_sync_for_target,
            validated_domain,
            pinned_ip,
        )
    return domain, info


async def _get_or_start_certificate_fetch(domain: str) -> tuple[str, CertificateInfo]:
    async with _cert_cache_lock:
        task = _cert_fetch_tasks.get(domain)
        if task is None:
            task = asyncio.create_task(_fetch_remote_certificate(domain))
            _cert_fetch_tasks[domain] = task

    try:
        return await asyncio.shield(task)
    finally:
        async with _cert_cache_lock:
            if _cert_fetch_tasks.get(domain) is task:
                _cert_fetch_tasks.pop(domain, None)





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
    unique_domains = normalize_domains(domains)
    
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

    async with _cert_cache_lock:
        fetch_generations = {d: _cert_cache_generations.get(d, 0) for d in domains_to_fetch}

    tasks = [_get_or_start_certificate_fetch(domain) for domain in domains_to_fetch]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    async with _cert_cache_lock:
        cached_at = datetime.now(UTC)
        for i, result in enumerate(results):
            domain = domains_to_fetch[i]
            if isinstance(result, Exception):
                logger.error("Certificate fetch failed for %s: %s", domain, result)
                info = _certificate_fetch_error_info(
                    status="error",
                    error_message=_build_certificate_fetch_error_message(result),
                    checked_at=cached_at,
                )
            else:
                info = result[1]

            cert_info[domain] = info
            if _cert_cache_generations.get(domain, 0) == fetch_generations[domain]:
                _cert_cache[domain] = (cached_at, info)
        _prune_cert_cache()

    return cert_info


async def get_cached_certificate_info_for_domains(
    domains: list[str],
    *,
    allow_stale: bool = True,
) -> dict[str, CertificateInfo]:
    """Return cached certificate info without triggering remote network fetches."""
    unique_domains = normalize_domains(domains)
    if not unique_domains:
        return {}

    async with _cert_cache_lock:
        cached_results: dict[str, CertificateInfo] = {}
        for domain in unique_domains:
            cached = _cert_cache.get(domain)
            if cached is None:
                continue
            cached_at, info = cached
            if allow_stale or _is_cache_valid(cached_at):
                cached_results[domain] = info
    return cached_results



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


def has_local_certificate_for_domain_checked(certificates_path: Path | None, domain: str) -> tuple[bool, bool]:
    """Return (has_certificate, storage_error) for callers that must not collapse I/O errors to missing."""
    normalized_domain = domain.strip().lower()
    if not normalized_domain or certificates_path is None:
        return False, False
    try:
        certs, storage_error = scan_certificate_storage(certificates_path)
        if storage_error:
            return False, True
        return find_certificate_for_domain(certs, normalized_domain) is not None, False
    except (OSError, PermissionError):
        return False, True


def _with_diagnostic(info: CertificateInfo, diagnostic: str) -> CertificateInfo:
    return replace(
        info,
        diagnostics=tuple(dict.fromkeys((*info.diagnostics, diagnostic))),
        local_artifact_present=False,
        local_artifact_complete=False,
    )


async def get_certificate_info_for_domains(
    domains: list[str],
    *,
    managed_site_states: dict[str, tuple[bool, datetime | None]] | None = None,
) -> dict[str, CertificateInfo]:
    """Get certificate info for multiple domains, preferring local Caddy storage.

    Resolution order: local storage → remote-result cache → live TLS fetch.
    All return paths pass through ``_apply_pending_status`` so that freshly
    created sites can transition from error/missing to pending.
    """
    unique_domains = normalize_domains(domains)
    if not unique_domains:
        return {}

    # 1. Local storage first — always authoritative when readable.
    local_results, storage_error = await asyncio.to_thread(
        get_local_certificate_info_for_domains, unique_domains
    )
    remaining = [d for d in unique_domains if d not in local_results]

    # 2. For domains without a local match: try cached remote results / live TLS.
    remote_results: dict[str, CertificateInfo] = {}
    if remaining:
        remote_raw = await get_certificate_info_for_domains_remote(remaining)
        remote_results = {
            domain: _with_diagnostic(info, "local_artifact_missing")
            if info.valid else info
            for domain, info in remote_raw.items()
        }

    combined = {**local_results, **remote_results}

    # 3. If storage is unreadable, mark domains that were not successfully
    #    validated remotely as storage_unavailable so they are not silently
    #    treated as "missing".
    if storage_error:
        now = datetime.now(UTC)
        for domain in remaining:
            if domain in combined and combined[domain].valid:
                continue
            combined[domain] = CertificateInfo(
                exists=False,
                status="storage_unavailable",
                error_message="Certificate storage could not be read.",
                checked_at=now,
            )

    return _apply_pending_status(combined, managed_site_states=managed_site_states)


def _apply_pending_status(
    certificate_info: dict[str, CertificateInfo],
    *,
    managed_site_states: dict[str, tuple[bool, datetime | None]] | None,
) -> dict[str, CertificateInfo]:
    if not managed_site_states:
        return certificate_info

    now = datetime.now(UTC)
    updated_results = dict(certificate_info)
    for domain, info in certificate_info.items():
        managed_state = managed_site_states.get(domain)
        if managed_state is None:
            continue
        enabled, updated_at = managed_state
        if not _is_pending_certificate_request(enabled=enabled, updated_at=updated_at, now=now):
            continue
        if info.valid or info.status == "remote_check_unavailable" or info.status == "storage_unavailable":
            continue
        updated_results[domain] = replace(
            info,
            exists=False,
            valid=False,
            status="pending",
            checked_at=now,
            diagnostics=tuple(dict.fromkeys((*info.diagnostics, "pending_after_site_update"))),
        )
    return updated_results

async def get_caddy_status(session: AsyncSession) -> HostServiceMetrics:
    """Get current Caddy service status for API endpoint."""
    return await _get_caddy_service_metrics(session)


def _collect_domain_counts(sites: Iterable[SiteLike]) -> tuple[int, int]:
    sites_list = list(sites)
    all_domains = set(normalize_domains([
        domain_name
        for site in sites_list
        for domain_name in split_domain_names(site.domain)
    ]))
    enabled_domains = set(normalize_domains([
        domain_name
        for site in sites_list
        if site.enabled
        for domain_name in split_domain_names(site.domain)
    ]))
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

    managed_site_states: dict[str, tuple[bool, datetime | None]] = {}
    all_domains: list[str] = []
    for site in sites:
        for normalized in normalize_domains(split_domain_names(site.domain)):
            all_domains.append(normalized)
            managed_site_states[normalized] = (site.enabled, getattr(site, "updated_at", None))
    all_domains = sorted(set(all_domains))

    valid_certificate_count = 0
    expired_certificate_count = 0
    expiring_soon_certificate_count = 0

    if all_domains:
        cert_info = await get_certificate_info_for_domains(
            all_domains, managed_site_states=managed_site_states
        )
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

