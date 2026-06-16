#!/usr/bin/env python3
#
# app/services/certificates.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Literal
from cryptography import x509

logger = logging.getLogger(__name__)

_MAX_CERTIFICATE_FILE_BYTES = 1024 * 1024
_MAX_CERT_INDEX_SCAN_FILES = 20_000


@dataclass(slots=True, frozen=True)
class CertificateInfo:
    """SSL certificate state for a single domain.

    `valid` means the certificate is within its validity window AND covers this
    domain (exact, SAN, or wildcard). `status` carries the higher-level state
    surfaced in the GUI; `source`/`match_type` record provenance for GUI text,
    renew decisions, and debugging.
    """
    exists: bool
    valid: bool = False
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    days_remaining: int | None = None
    error_message: str | None = None
    # valid | expired | pending | error | missing | storage_unavailable | remote_check_unavailable
    status: str = "missing"
    source: str = "none"  # local | remote | none
    match_type: str | None = None  # san | wildcard
    is_wildcard: bool = False
    covering_name: str | None = None  # e.g. "*.cirrio.de"
    checked_at: datetime | None = None
    diagnostics: tuple[str, ...] = ()
    local_artifact_present: bool = False
    local_artifact_complete: bool = False
    artifact_scope_name: str | None = None


@dataclass(frozen=True, slots=True)
class CertificateRenewalCapability:
    mode: Literal[
        "artifact_purge",
        "api_reload",
        "restart_repair",
        "wildcard_scope_required",
        "acquisition_sync",
        "storage_unavailable",
        "unavailable",
    ]
    reason: str
    requires_confirmation: bool
    scope_name: str | None = None
    scope_type: Literal["domain", "wildcard"] = "domain"
    wait_domains: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class CertificateName:
    value: str
    source: str  # "san" | "cn"


@dataclass(slots=True, frozen=True)
class ParsedCertificate:
    path: Path
    dns_names: tuple[CertificateName, ...]
    issued_at: datetime | None
    expires_at: datetime | None
    local_artifact_complete: bool = True
    artifact_scope_name: str | None = None


def normalize_domains(domains: Iterable[str]) -> list[str]:
    return sorted({domain.lower().strip() for domain in domains if domain and domain.strip()})


def certificate_names(certificate: x509.Certificate) -> tuple[CertificateName, ...]:
    """Return the SAN DNS names a certificate is valid for."""
    names: list[CertificateName] = []
    try:
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        for name in san.value.get_values_for_type(x509.DNSName):
            val = name.lower().strip()
            if val:
                names.append(CertificateName(value=val, source="san"))
    except x509.ExtensionNotFound:
        pass
    return tuple(names)


def dns_name_covers(cert_name: str, domain: str) -> tuple[bool, str | None]:
    """Return whether a single certificate DNS name covers a domain.

    Wildcards match exactly one additional label: ``*.cirrio.de`` covers
    ``grafana.cirrio.de`` but neither ``cirrio.de`` nor ``a.b.cirrio.de``.
    """
    cert_name = cert_name.lower().strip()
    domain = domain.lower().strip()
    if not cert_name or not domain:
        return False, None
    if cert_name == domain:
        return True, "exact"
    if cert_name.startswith("*."):
        suffix = cert_name[1:]  # ".cirrio.de"
        if domain.endswith(suffix):
            remainder = domain[: -len(suffix)]
            if remainder and "." not in remainder:
                return True, "wildcard"
    return False, None


def certificate_coverage_for_domain(
    certificate: x509.Certificate,
    domain: str,
) -> tuple[bool, str | None, str | None, bool]:
    """Return (covers, match_type, covering_name, is_wildcard)."""
    normalized_domain = domain.lower().strip()
    if not normalized_domain:
        return False, None, None, False

    cert_names = certificate_names(certificate)

    wildcard_match: str | None = None
    for cert_name in cert_names:
        covers, match_type = dns_name_covers(cert_name.value, normalized_domain)
        if not covers:
            continue
        if match_type == "wildcard":
            wildcard_match = wildcard_match or cert_name.value
            continue
        return True, cert_name.source, cert_name.value, False

    if wildcard_match is not None:
        return True, "wildcard", wildcard_match, True
    return False, None, None, False


def load_x509_certificate_bytes(certificate_bytes: bytes) -> x509.Certificate | None:
    try:
        return x509.load_pem_x509_certificate(certificate_bytes)
    except ValueError:
        try:
            return x509.load_der_x509_certificate(certificate_bytes)
        except ValueError:
            return None


def load_x509_certificate_from_path(certificate_path: Path) -> x509.Certificate | None:
    with certificate_path.open("rb") as certificate_file:
        certificate_bytes = certificate_file.read(_MAX_CERTIFICATE_FILE_BYTES + 1)

    if len(certificate_bytes) > _MAX_CERTIFICATE_FILE_BYTES:
        logger.warning("Skipping oversized certificate file: %s", certificate_path)
        return None

    return load_x509_certificate_bytes(certificate_bytes)


def artifact_scope_from_cert_path(cert_path: Path, certificates_path: Path) -> str | None:
    try:
        relative_path = cert_path.resolve(strict=False).relative_to(certificates_path.resolve(strict=False))
    except ValueError:
        return None

    if len(relative_path.parts) != 3:
        return None

    issuer_name, scope_name, filename = relative_path.parts
    issuer_name = issuer_name.strip().lower()
    scope_name = scope_name.strip().lower()
    filename = filename.strip().lower()
    if not issuer_name or not scope_name:
        return None
    if issuer_name != "acme" and not issuer_name.endswith("-directory"):
        return None
    if filename != f"{scope_name}.crt":
        return None

    return scope_name


def certificate_info_from_dates(
    issued_at: datetime | None,
    expires_at: datetime | None,
    now: datetime,
    *,
    status: str | None = None,
    source: str = "none",
    match_type: str | None = None,
    is_wildcard: bool = False,
    covering_name: str | None = None,
    checked_at: datetime | None = None,
    error_message: str | None = None,
    diagnostics: tuple[str, ...] = (),
    local_artifact_present: bool = False,
    local_artifact_complete: bool = False,
    artifact_scope_name: str | None = None,
) -> CertificateInfo | None:
    if expires_at is None:
        return None

    seconds_remaining = int((expires_at - now).total_seconds())
    valid_from_ok = issued_at is None or issued_at <= now
    is_valid = valid_from_ok and seconds_remaining > 0
    resolved_status = status
    if resolved_status is None:
        if is_valid:
            resolved_status = "valid"
        elif issued_at is not None and issued_at > now:
            resolved_status = "pending"
        else:
            resolved_status = "expired"
    return CertificateInfo(
        exists=True,
        valid=is_valid,
        status=resolved_status,
        source=source,
        match_type=match_type,
        is_wildcard=is_wildcard,
        covering_name=covering_name,
        checked_at=checked_at,
        error_message=error_message,
        issued_at=issued_at,
        expires_at=expires_at,
        days_remaining=math.ceil(seconds_remaining / 86400) if seconds_remaining > 0 else 0,
        diagnostics=diagnostics,
        local_artifact_present=local_artifact_present,
        local_artifact_complete=local_artifact_complete,
        artifact_scope_name=artifact_scope_name,
    )


def scan_certificate_storage(certificates_path: Path | None) -> tuple[list[ParsedCertificate], bool]:
    if certificates_path is None:
        return [], True

    try:
        if not certificates_path.exists():
            logger.warning("Certificate storage path does not exist: %s", certificates_path)
            return [], True
        if not certificates_path.is_dir():
            logger.warning("Certificate storage path is not a directory: %s", certificates_path)
            return [], True
    except (OSError, PermissionError) as exc:
        logger.warning("Could not inspect certificate storage path %s: %s", certificates_path, exc)
        return [], True

    parsed: list[ParsedCertificate] = []
    storage_error = False
    try:
        cert_iter = certificates_path.rglob("*.crt")
        for checked, cert_path in enumerate(cert_iter, start=1):
            if checked > _MAX_CERT_INDEX_SCAN_FILES:
                logger.warning("Certificate index scan limit exceeded: %s", certificates_path)
                storage_error = True
                break
            try:
                if not cert_path.is_file():
                    continue
                certificate = load_x509_certificate_from_path(cert_path)
                if certificate is None:
                    continue
                artifact_scope = artifact_scope_from_cert_path(cert_path, certificates_path)
                key_path = cert_path.with_suffix(".key")
                json_path = cert_path.with_suffix(".json")
                complete = artifact_scope is not None and key_path.is_file() and json_path.is_file()

                parsed.append(
                    ParsedCertificate(
                        path=cert_path,
                        dns_names=certificate_names(certificate),
                        issued_at=certificate.not_valid_before_utc,
                        expires_at=certificate.not_valid_after_utc,
                        local_artifact_complete=complete,
                        artifact_scope_name=artifact_scope,
                    )
                )
            except (OSError, PermissionError) as exc:
                storage_error = True
                logger.warning("Could not inspect certificate file %s: %s", cert_path, exc)
            except Exception as exc:
                logger.debug("Skipping certificate file after parse failure: %s (%s)", cert_path, exc)
                continue
    except (OSError, PermissionError) as exc:
        logger.warning("Could not scan certificate storage %s: %s", certificates_path, exc)
        storage_error = True

    return parsed, storage_error


def find_certificate_for_domain(certs: list[ParsedCertificate], domain: str) -> CertificateInfo | None:
    normalized_domain = domain.lower().strip()
    if not normalized_domain:
        return None

    now = datetime.now(UTC)
    best_match: tuple[tuple[int, int, int, float], CertificateInfo] | None = None
    for cert in certs:
        covering_name: str | None = None
        match_type: str | None = None
        is_wildcard = False
        for cert_name in cert.dns_names:
            covers, name_match_type = dns_name_covers(cert_name.value, normalized_domain)
            if not covers:
                continue
            if name_match_type == "wildcard":
                if covering_name is None:
                    covering_name = cert_name.value
                    match_type = "wildcard"
                    is_wildcard = True
                continue
            covering_name = cert_name.value
            match_type = cert_name.source
            is_wildcard = False
            break

        if covering_name is None or match_type is None:
            continue

        certificate_info = certificate_info_from_dates(
            cert.issued_at,
            cert.expires_at,
            now,
            source="local",
            match_type=match_type,
            is_wildcard=is_wildcard,
            covering_name=covering_name,
            checked_at=now,
            local_artifact_present=True,
            local_artifact_complete=cert.local_artifact_complete,
            artifact_scope_name=cert.artifact_scope_name,
        )
        if certificate_info is None:
            continue

        rank = (
            0 if certificate_info.valid else 1,
            0 if certificate_info.local_artifact_complete else 1,
            0 if match_type == "san" and not is_wildcard else 1,
            -(cert.expires_at.timestamp() if cert.expires_at else 0),
        )
        if best_match is None or rank < best_match[0]:
            best_match = (rank, certificate_info)

    return best_match[1] if best_match else None
