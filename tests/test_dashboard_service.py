#!/usr/bin/env python3
#
# tests/test_dashboard_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import os
import socket
import ssl
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config.settings import get_settings

_ENV_OVERRIDES = {
    "CB_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key-for-testing",
}
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}

for key, value in _ENV_OVERRIDES.items():
    os.environ[key] = value

get_settings.cache_clear()

import app.services.dashboard as dashboard_module
import app.services.certificates as certs_module


class DashboardServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async with dashboard_module._cert_cache_lock:
            dashboard_module._cert_cache.clear()
            dashboard_module._cert_fetch_tasks.clear()

    async def asyncTearDown(self) -> None:
        async with dashboard_module._cert_cache_lock:
            dashboard_module._cert_cache.clear()
            dashboard_module._cert_fetch_tasks.clear()

    async def test_get_dashboard_metrics_aggregates_domains_and_certificate_counts(self) -> None:
        sites = [
            SimpleNamespace(domain="valid.example.com", enabled=True),
            SimpleNamespace(domain="expired.example.com", enabled=False),
        ]

        with (
            patch.object(dashboard_module.site_repository, "list_all", new=AsyncMock(return_value=sites)),
            patch.object(
                dashboard_module,
                "get_certificate_info_for_domains",
                new=AsyncMock(
                    return_value={
                        "valid.example.com": dashboard_module.CertificateInfo(exists=True, valid=True, days_remaining=10),
                        "expired.example.com": dashboard_module.CertificateInfo(exists=True, valid=False, days_remaining=0),
                    }
                ),
            ),
            patch.object(
                dashboard_module,
                "_get_caddy_service_metrics",
                return_value=dashboard_module.HostServiceMetrics(status="Running", uptime="2h 15m", version="v2.8.4"),
            ),
        ):
            metrics = await dashboard_module.get_dashboard_metrics(SimpleNamespace())

        self.assertEqual(metrics.domain_count, 2)
        self.assertEqual(metrics.enabled_domain_count, 1)
        self.assertEqual(metrics.valid_certificate_count, 1)
        self.assertEqual(metrics.expired_certificate_count, 1)
        self.assertEqual(metrics.expiring_soon_certificate_count, 0)
        self.assertEqual(metrics.caddy_service_status, "Running")
        self.assertEqual(metrics.caddy_service_uptime, "2h 15m")
        self.assertEqual(metrics.caddy_version, "v2.8.4")

    async def test_get_dashboard_metrics_counts_certificates_expiring_within_seven_days(self) -> None:
        sites = [SimpleNamespace(domain="soon.example.com", enabled=True)]

        with (
            patch.object(dashboard_module.site_repository, "list_all", new=AsyncMock(return_value=sites)),
            patch.object(
                dashboard_module,
                "get_certificate_info_for_domains",
                new=AsyncMock(
                    return_value={
                        "soon.example.com": dashboard_module.CertificateInfo(
                            exists=True,
                            valid=True,
                            days_remaining=3,
                        ),
                    }
                ),
            ),
            patch.object(
                dashboard_module,
                "_get_caddy_service_metrics",
                return_value=dashboard_module.HostServiceMetrics(status="Running", uptime="2h 15m", version="v2.8.4"),
            ),
        ):
            metrics = await dashboard_module.get_dashboard_metrics(SimpleNamespace())

        self.assertEqual(metrics.valid_certificate_count, 1)
        self.assertEqual(metrics.expiring_soon_certificate_count, 1)

    async def test_get_dashboard_metrics_treats_seven_days_as_expiring_soon_boundary(self) -> None:
        sites = [SimpleNamespace(domain="boundary.example.com", enabled=True)]

        with (
            patch.object(dashboard_module.site_repository, "list_all", new=AsyncMock(return_value=sites)),
            patch.object(
                dashboard_module,
                "get_certificate_info_for_domains",
                new=AsyncMock(
                    return_value={
                        "boundary.example.com": dashboard_module.CertificateInfo(
                            exists=True,
                            valid=True,
                            days_remaining=7,
                        ),
                    }
                ),
            ),
            patch.object(
                dashboard_module,
                "_get_caddy_service_metrics",
                return_value=dashboard_module.HostServiceMetrics(status="Running", uptime="2h 15m", version="v2.8.4"),
            ),
        ):
            metrics = await dashboard_module.get_dashboard_metrics(SimpleNamespace())

        self.assertEqual(metrics.valid_certificate_count, 1)
        self.assertEqual(metrics.expiring_soon_certificate_count, 1)

    async def test_get_dashboard_metrics_excludes_certificates_beyond_seven_days(self) -> None:
        sites = [SimpleNamespace(domain="later.example.com", enabled=True)]

        with (
            patch.object(dashboard_module.site_repository, "list_all", new=AsyncMock(return_value=sites)),
            patch.object(
                dashboard_module,
                "get_certificate_info_for_domains",
                new=AsyncMock(
                    return_value={
                        "later.example.com": dashboard_module.CertificateInfo(
                            exists=True,
                            valid=True,
                            days_remaining=8,
                        ),
                    }
                ),
            ),
            patch.object(
                dashboard_module,
                "_get_caddy_service_metrics",
                return_value=dashboard_module.HostServiceMetrics(status="Running", uptime="2h 15m", version="v2.8.4"),
            ),
        ):
            metrics = await dashboard_module.get_dashboard_metrics(SimpleNamespace())

        self.assertEqual(metrics.valid_certificate_count, 1)
        self.assertEqual(metrics.expiring_soon_certificate_count, 0)

    async def test_get_dashboard_metrics_keeps_unknown_status_when_api_version_unavailable(self) -> None:
        with (
            patch.object(dashboard_module.site_repository, "list_all", new=AsyncMock(return_value=[])),
            patch.object(
                dashboard_module,
                "_get_caddy_service_metrics",
                return_value=dashboard_module.HostServiceMetrics(
                    status="Unknown",
                    uptime="Unavailable",
                    version="Unavailable",
                ),
            ),
        ):
            metrics = await dashboard_module.get_dashboard_metrics(SimpleNamespace())

        self.assertEqual(metrics.caddy_service_status, "Unknown")
        self.assertEqual(metrics.caddy_service_uptime, "Unavailable")
        self.assertEqual(metrics.caddy_version, "Unavailable")
        self.assertEqual(metrics.expiring_soon_certificate_count, 0)

    async def test_get_certificate_info_for_domains_prefers_local_storage(self) -> None:
        local_info = dashboard_module.CertificateInfo(
            exists=True,
            valid=True,
            issued_at=datetime(2026, 5, 11, tzinfo=UTC),
            expires_at=datetime(2026, 8, 11, tzinfo=UTC),
            days_remaining=69,
        )

        with (
            patch.object(
                dashboard_module,
                "get_local_certificate_info_for_domains",
                return_value=({"dav.cirrio.de": local_info}, False),
            ),
            patch.object(
                dashboard_module,
                "get_certificate_info_for_domains_remote",
                new=AsyncMock(return_value={}),
            ) as remote_mock,
        ):
            result = await dashboard_module.get_certificate_info_for_domains(["dav.cirrio.de"])

        self.assertEqual(result, {"dav.cirrio.de": local_info})
        remote_mock.assert_not_awaited()

    def test_certificate_info_from_dates_requires_not_before(self) -> None:
        now = datetime(2026, 6, 3, tzinfo=UTC)

        info = certs_module.certificate_info_from_dates(
            issued_at=now + timedelta(days=1),
            expires_at=now + timedelta(days=30),
            now=now,
        )

        self.assertIsNotNone(info)
        self.assertFalse(info.valid)
        self.assertEqual(info.days_remaining, 30)

    def test_dns_name_covers_supports_exact_and_single_label_wildcards(self) -> None:
        self.assertEqual(certs_module.dns_name_covers("example.com", "example.com"), (True, "exact"))
        self.assertEqual(
            certs_module.dns_name_covers("*.example.com", "grafana.example.com"),
            (True, "wildcard"),
        )
        self.assertEqual(certs_module.dns_name_covers("*.example.com", "example.com"), (False, None))
        self.assertEqual(certs_module.dns_name_covers("*.example.com", "a.b.example.com"), (False, None))

    def test_match_local_certificate_prefers_exact_match_over_wildcard(self) -> None:
        now = datetime.now(UTC)
        exact = certs_module.ParsedCertificate(
            path=Path("/tmp/certificates/example.com/example.com.crt"),
            dns_names=(certs_module.CertificateName(value="example.com", source="san"),),
            issued_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=30),
            artifact_scope_name="example.com",
        )
        wildcard = certs_module.ParsedCertificate(
            path=Path("/tmp/certificates/wildcard_example_com/wildcard_example_com.crt"),
            dns_names=(certs_module.CertificateName(value="*.example.com", source="san"),),
            issued_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=60),
            artifact_scope_name="wildcard_example_com",
        )

        info = certs_module.find_certificate_for_domain([wildcard, exact], "example.com")

        self.assertIsNotNone(info)
        self.assertEqual(info.covering_name, "example.com")
        self.assertEqual(info.match_type, "san")
        self.assertFalse(info.is_wildcard)

    def test_apply_pending_status_marks_recent_enabled_sites(self) -> None:
        now = datetime.now(UTC)
        certificate_info = {
            "example.com": dashboard_module.CertificateInfo(
                exists=False,
                status="error",
                error_message="Certificate check failed.",
            )
        }

        result = dashboard_module._apply_pending_status(
            certificate_info,
            managed_site_states={"example.com": (True, now - timedelta(minutes=15))},
        )

        self.assertEqual(result["example.com"].status, "pending")
        self.assertFalse(result["example.com"].exists)

    def test_match_local_certificate_valid_wildcard_beats_expired_exact(self) -> None:
        now = datetime.now(UTC)
        expired_exact = certs_module.ParsedCertificate(
            path=Path("/tmp/certificates/grafana.example.com/grafana.example.com.crt"),
            dns_names=(certs_module.CertificateName(value="grafana.example.com", source="san"),),
            issued_at=now - timedelta(days=100),
            expires_at=now - timedelta(days=1),  # expired
            artifact_scope_name="grafana.example.com",
        )
        valid_wildcard = certs_module.ParsedCertificate(
            path=Path("/tmp/certificates/wildcard_.example.com/wildcard_.example.com.crt"),
            dns_names=(certs_module.CertificateName(value="*.example.com", source="san"),),
            issued_at=now - timedelta(days=10),
            expires_at=now + timedelta(days=60),  # valid
            artifact_scope_name="wildcard_.example.com",
        )

        info = certs_module.find_certificate_for_domain(
            [expired_exact, valid_wildcard], "grafana.example.com"
        )

        self.assertIsNotNone(info)
        self.assertTrue(info.valid)
        self.assertTrue(info.is_wildcard)
        self.assertEqual(info.covering_name, "*.example.com")
        self.assertEqual(info.match_type, "wildcard")

    def test_match_local_certificate_prefers_complete_artifact_over_longer_expiry(self) -> None:
        now = datetime.now(UTC)
        incomplete_newer = certs_module.ParsedCertificate(
            path=Path("/tmp/certificates/example.com/example.com.crt"),
            dns_names=(certs_module.CertificateName(value="example.com", source="san"),),
            issued_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=90),
            local_artifact_complete=False,
            artifact_scope_name="example.com",
        )
        complete_older = certs_module.ParsedCertificate(
            path=Path("/tmp/certificates/example.com/example.com.crt"),
            dns_names=(certs_module.CertificateName(value="example.com", source="san"),),
            issued_at=now - timedelta(days=10),
            expires_at=now + timedelta(days=30),
            local_artifact_complete=True,
            artifact_scope_name="example.com",
        )

        info = certs_module.find_certificate_for_domain([incomplete_newer, complete_older], "example.com")

        self.assertIsNotNone(info)
        self.assertTrue(info.local_artifact_complete)
        self.assertEqual(info.expires_at, complete_older.expires_at)

    def test_match_local_certificate_does_not_guess_artifact_scope_from_unstructured_path(self) -> None:
        now = datetime.now(UTC)
        cert = certs_module.ParsedCertificate(
            path=Path("/tmp/certificates/archive/example.com.crt"),
            dns_names=(certs_module.CertificateName(value="example.com", source="san"),),
            issued_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=30),
            local_artifact_complete=False,
            artifact_scope_name=None,
        )

        info = certs_module.find_certificate_for_domain([cert], "example.com")

        self.assertIsNotNone(info)
        self.assertIsNone(info.artifact_scope_name)

    def test_artifact_scope_from_cert_path_requires_expected_caddy_layout(self) -> None:
        self.assertEqual(
            certs_module.artifact_scope_from_cert_path(
                Path("/var/lib/caddy/certificates/acme/example.com/example.com.crt"),
                Path("/var/lib/caddy/certificates"),
            ),
            "example.com",
        )
        self.assertIsNone(
            certs_module.artifact_scope_from_cert_path(
                Path("/var/lib/caddy/certificates/acme/archive/example.com.crt"),
                Path("/var/lib/caddy/certificates"),
            )
        )
        self.assertIsNone(
            certs_module.artifact_scope_from_cert_path(
                Path("/var/lib/caddy/certificates/archive/example.com/example.com.crt"),
                Path("/var/lib/caddy/certificates"),
            )
        )

    def test_scan_certificate_storage_marks_storage_error_for_missing_or_non_directory_path(self) -> None:
        with patch.object(Path, "exists", return_value=False):
            parsed, storage_error = certs_module.scan_certificate_storage(Path("/certs"))

        self.assertEqual(parsed, [])
        self.assertTrue(storage_error)

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "is_dir", return_value=False),
        ):
            parsed, storage_error = certs_module.scan_certificate_storage(Path("/certs"))

        self.assertEqual(parsed, [])
        self.assertTrue(storage_error)

    def test_scan_certificate_storage_marks_storage_error_when_scan_limit_is_exceeded(self) -> None:
        cert_path = Path("/certs/example.com/example.com.crt")
        root = Path("/certs")
        certificate = SimpleNamespace(
            not_valid_before_utc=datetime.now(UTC) - timedelta(days=1),
            not_valid_after_utc=datetime.now(UTC) + timedelta(days=30),
        )

        with (
            patch.object(certs_module, "_MAX_CERT_INDEX_SCAN_FILES", 0),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "is_dir", return_value=True),
            patch.object(Path, "rglob", return_value=iter([cert_path])),
            patch.object(Path, "is_file", return_value=True),
            patch.object(certs_module, "load_x509_certificate_from_path", return_value=certificate),
        ):
            parsed, storage_error = certs_module.scan_certificate_storage(root)

        self.assertEqual(parsed, [])
        self.assertTrue(storage_error)

    def test_load_x509_certificate_from_path_raises_oserror_for_unreadable_file(self) -> None:
        cert_path = Path("/tmp/unreadable.crt")
        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                certs_module.load_x509_certificate_from_path(cert_path)

    def test_scan_certificate_storage_marks_storage_error_for_unreadable_certificate_file(self) -> None:
        cert_path = Path("/certs/example.com/example.com.crt")
        root = Path("/certs")

        with (
            patch.object(Path, "is_dir", return_value=True),
            patch.object(Path, "rglob", return_value=iter([cert_path])),
            patch.object(Path, "is_file", return_value=True),
            patch.object(certs_module, "load_x509_certificate_from_path", side_effect=PermissionError("denied")),
        ):
            parsed, storage_error = certs_module.scan_certificate_storage(root)

        self.assertEqual(parsed, [])
        self.assertTrue(storage_error)

    def test_scan_certificate_storage_marks_storage_error_for_rglob_iteration_failure(self) -> None:
        class BrokenIterator:
            def __iter__(self):
                return self

            def __next__(self):
                raise PermissionError("denied")

        root = Path("/certs")
        with (
            patch.object(Path, "is_dir", return_value=True),
            patch.object(Path, "rglob", return_value=BrokenIterator()),
        ):
            parsed, storage_error = certs_module.scan_certificate_storage(root)

        self.assertEqual(parsed, [])
        self.assertTrue(storage_error)

    def test_scan_certificate_storage_only_marks_artifact_complete_for_recognized_scope(self) -> None:
        cert_path = Path("/certs/archive/example.com.crt")
        root = Path("/certs")
        certificate = SimpleNamespace(
            not_valid_before_utc=datetime.now(UTC) - timedelta(days=1),
            not_valid_after_utc=datetime.now(UTC) + timedelta(days=30),
        )

        def is_file_side_effect(self):
            return self in {cert_path, cert_path.with_suffix(".key"), cert_path.with_suffix(".json")}

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "is_dir", return_value=True),
            patch.object(Path, "rglob", return_value=iter([cert_path])),
            patch.object(Path, "is_file", is_file_side_effect),
            patch.object(certs_module, "load_x509_certificate_from_path", return_value=certificate),
            patch.object(certs_module, "certificate_names", return_value=(certs_module.CertificateName(value="example.com", source="san"),)),
        ):
            parsed, storage_error = certs_module.scan_certificate_storage(root)

        self.assertFalse(storage_error)
        self.assertEqual(len(parsed), 1)
        self.assertFalse(parsed[0].local_artifact_complete)
        self.assertIsNone(parsed[0].artifact_scope_name)

    async def test_pending_applied_when_all_domains_found_locally(self) -> None:
        """Pending status must be applied even when all domains resolve locally."""
        local_error = dashboard_module.CertificateInfo(
            exists=False,
            status="error",
            error_message="cert check failed",
        )
        managed_states = {"example.com": (True, datetime.now(UTC) - timedelta(minutes=15))}

        with patch.object(
            dashboard_module,
            "get_local_certificate_info_for_domains",
            return_value=({"example.com": local_error}, False),
        ):
            result = await dashboard_module.get_certificate_info_for_domains(
                ["example.com"], managed_site_states=managed_states
            )

        self.assertEqual(result["example.com"].status, "pending")

    async def test_fetch_remote_certificate_marks_private_host_checks_unavailable(self) -> None:
        with patch.object(
            dashboard_module,
            "_resolve_public_certificate_target",
            side_effect=ValueError("Certificate checks require a public hostname."),
        ):
            domain, info = await dashboard_module._fetch_remote_certificate("dashboard.example.com")

        self.assertEqual(domain, "dashboard.example.com")
        self.assertFalse(info.exists)
        self.assertEqual(info.status, "remote_check_unavailable")
        self.assertEqual(info.error_message, "Certificate checks require a public hostname.")

    async def test_fetch_remote_certificate_marks_dns_lookup_failure_unavailable(self) -> None:
        with patch.object(
            dashboard_module,
            "_resolve_public_certificate_target",
            side_effect=socket.gaierror(8, "Name or service not known"),
        ):
            domain, info = await dashboard_module._fetch_remote_certificate("dashboard.example.com")

        self.assertEqual(domain, "dashboard.example.com")
        self.assertFalse(info.exists)
        self.assertEqual(info.status, "remote_check_unavailable")
        self.assertEqual(info.error_message, "DNS lookup failed for this domain.")

    async def test_get_certificate_info_for_domains_returns_storage_unavailable_on_read_error(self) -> None:
        with (
            patch.object(
                dashboard_module,
                "get_local_certificate_info_for_domains",
                return_value=({}, True),
            ),
            patch.object(
                dashboard_module,
                "get_certificate_info_for_domains_remote",
                new=AsyncMock(return_value={}),
            ) as remote_mock,
        ):
            result = await dashboard_module.get_certificate_info_for_domains(["example.com"])

        remote_mock.assert_awaited_once_with(["example.com"])
        self.assertEqual(result["example.com"].status, "storage_unavailable")
        self.assertFalse(result["example.com"].exists)

    async def test_get_certificate_info_for_domains_allows_remote_fallback_on_storage_error(self) -> None:
        valid_remote_cert = dashboard_module.CertificateInfo(
            exists=True,
            valid=True,
            status="valid",
            checked_at=datetime.now(UTC),
        )
        with (
            patch.object(
                dashboard_module,
                "get_local_certificate_info_for_domains",
                return_value=({}, True),
            ),
            patch.object(
                dashboard_module,
                "get_certificate_info_for_domains_remote",
                new=AsyncMock(return_value={"example.com": valid_remote_cert}),
            ) as remote_mock,
        ):
            result = await dashboard_module.get_certificate_info_for_domains(["example.com"])

        remote_mock.assert_awaited_once_with(["example.com"])
        self.assertTrue(result["example.com"].valid)
        self.assertEqual(result["example.com"].status, "valid")

    async def test_get_certificate_info_for_domains_remote_marks_task_exceptions_as_error(self) -> None:
        with patch.object(
            dashboard_module,
            "_get_or_start_certificate_fetch",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await dashboard_module.get_certificate_info_for_domains_remote(["example.com"])

        info = result["example.com"]
        self.assertEqual(info.status, "error")
        self.assertFalse(info.exists)
        self.assertIsNotNone(info.checked_at)
        self.assertIn("boom", info.error_message or "")

    async def test_get_caddy_service_metrics_returns_running_when_admin_api_is_healthy(self) -> None:
        fake_client = AsyncMock()
        fake_client.health.return_value = True
        fake_client.get_version.return_value = "v2.8.4"
        fake_client.__aenter__.return_value = fake_client
        fake_client.__aexit__.return_value = None

        with (
            patch.object(
                dashboard_module,
                "get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019")),
            ),
            patch.object(
                dashboard_module,
                "get_settings",
                return_value=SimpleNamespace(
                    caddy_admin_url="http://localhost:2019",
                    caddy_admin_timeout_seconds=10.0,
                ),
            ),
            patch.object(dashboard_module, "CaddyAdminClient", return_value=fake_client) as client_cls,
        ):
            metrics = await dashboard_module._get_caddy_service_metrics(SimpleNamespace())

        client_cls.assert_called_once_with("http://localhost:2019", timeout_seconds=2.0)
        self.assertEqual(metrics.status, "Running")
        self.assertEqual(metrics.uptime, "Unavailable")
        self.assertEqual(metrics.version, "v2.8.4")

    async def test_get_caddy_service_metrics_returns_unknown_when_admin_api_is_unreachable(self) -> None:
        fake_client = AsyncMock()
        fake_client.health.side_effect = dashboard_module.CaddyServiceError("boom")
        fake_client.__aenter__.return_value = fake_client
        fake_client.__aexit__.return_value = None

        with (
            patch.object(
                dashboard_module,
                "get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019")),
            ),
            patch.object(
                dashboard_module,
                "get_settings",
                return_value=SimpleNamespace(
                    caddy_admin_url="http://localhost:2019",
                    caddy_admin_timeout_seconds=10.0,
                ),
            ),
            patch.object(dashboard_module, "CaddyAdminClient", return_value=fake_client),
        ):
            metrics = await dashboard_module._get_caddy_service_metrics(SimpleNamespace())

        self.assertEqual(metrics.status, "Unknown")
        self.assertEqual(metrics.uptime, "Unavailable")
        self.assertEqual(metrics.version, "Unavailable")

    async def test_get_caddy_service_metrics_uses_one_client_for_health_and_version(self) -> None:
        fake_client = AsyncMock()
        fake_client.health.return_value = True
        fake_client.get_version.return_value = None
        fake_client.__aenter__.return_value = fake_client
        fake_client.__aexit__.return_value = None

        with (
            patch.object(
                dashboard_module,
                "get_caddy_config",
                new=AsyncMock(return_value=SimpleNamespace(admin_url="http://localhost:2019")),
            ),
            patch.object(
                dashboard_module,
                "get_settings",
                return_value=SimpleNamespace(
                    caddy_admin_url="http://localhost:2019",
                    caddy_admin_timeout_seconds=10.0,
                ),
            ),
            patch.object(dashboard_module, "CaddyAdminClient", return_value=fake_client),
        ):
            metrics = await dashboard_module._get_caddy_service_metrics(SimpleNamespace())

        fake_client.health.assert_awaited_once()
        fake_client.get_version.assert_awaited_once()
        self.assertEqual(metrics.version, "Unavailable")

    async def test_get_certificate_info_for_domains_uses_fresh_cache(self) -> None:
        """Cache entries are only used for domains NOT found locally."""
        cached_info = dashboard_module.CertificateInfo(exists=True, valid=True, days_remaining=1)

        async with dashboard_module._cert_cache_lock:
            dashboard_module._cert_cache["example.com"] = (datetime.now(UTC), cached_info)

        with (
            patch.object(
                dashboard_module,
                "get_local_certificate_info_for_domains",
                return_value=({}, False),
            ),
        ):
            result = await dashboard_module.get_certificate_info_for_domains(["example.com"])

        # Domain not found locally -> served via remote (which hits cache)
        from dataclasses import replace
        expected_info = replace(
            cached_info,
            diagnostics=("local_artifact_missing",),
            local_artifact_present=False,
            local_artifact_complete=False,
        )
        self.assertEqual(result["example.com"], expected_info)

    async def test_get_certificate_info_for_domains_remote_deduplicates_inflight_fetches(self) -> None:
        fetched_info = dashboard_module.CertificateInfo(exists=True, valid=True, days_remaining=5)

        with patch.object(
            dashboard_module,
            "_fetch_remote_certificate",
            new=AsyncMock(return_value=("example.com", fetched_info)),
        ) as fetch_remote_mock:
            first, second = await asyncio.gather(
                dashboard_module.get_certificate_info_for_domains_remote(["example.com"]),
                dashboard_module.get_certificate_info_for_domains_remote(["example.com"]),
            )

        self.assertEqual(first, {"example.com": fetched_info})
        self.assertEqual(second, {"example.com": fetched_info})
        fetch_remote_mock.assert_awaited_once_with("example.com")

    async def test_get_cached_certificate_info_for_domains_returns_stale_entries_without_fetching(self) -> None:
        stale_info = dashboard_module.CertificateInfo(exists=True, valid=False, days_remaining=0)
        stale_cached_at = datetime.now(UTC) - timedelta(hours=2)

        async with dashboard_module._cert_cache_lock:
            dashboard_module._cert_cache["example.com"] = (stale_cached_at, stale_info)

        with patch.object(dashboard_module, "_fetch_remote_certificate", new=AsyncMock()) as fetch_remote_mock:
            result = await dashboard_module.get_cached_certificate_info_for_domains(
                ["example.com", "missing.example.com"],
                allow_stale=True,
            )

        fetch_remote_mock.assert_not_awaited()
        self.assertEqual(result, {"example.com": stale_info})

    def test_fetch_remote_certificate_sync_for_target_returns_error_message_and_logs_at_error(self) -> None:
        with (
            patch.object(
                dashboard_module.socket,
                "create_connection",
                side_effect=ssl.SSLError("[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error (_ssl.c:1029)"),
            ),
            self.assertLogs(dashboard_module.logger, level="WARNING") as log_capture,
        ):
            info = dashboard_module._fetch_remote_certificate_sync_for_target("example.com", "8.8.8.8")

        self.assertFalse(info.exists)
        self.assertEqual(
            info.error_message,
            "TLS handshake failed: the remote server aborted the connection with an internal TLS error.",
        )
        self.assertTrue(any("Failed to fetch certificate for example.com" in entry for entry in log_capture.output))

    def test_fetch_remote_certificate_sync_for_target_returns_explicit_error_when_dates_missing(self) -> None:
        class _FakeSSLSocket:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def getpeercert(self, binary_form: bool = False):
                return b"fake-cert" if binary_form else {}

        class _FakeSSLContext:
            check_hostname = True
            verify_mode = ssl.CERT_REQUIRED

            def wrap_socket(self, sock, server_hostname: str):
                return _FakeSSLSocket()

        with (
            patch.object(dashboard_module.ssl, "create_default_context", return_value=_FakeSSLContext()),
            patch.object(dashboard_module.socket, "create_connection", return_value=_FakeSSLSocket()),
            patch.object(
                dashboard_module.x509,
                "load_der_x509_certificate",
                return_value=SimpleNamespace(not_valid_before_utc=datetime.now(UTC), not_valid_after_utc=datetime.now(UTC) + timedelta(days=30)),
            ),
            patch.object(
                dashboard_module,
                "certificate_coverage_for_domain",
                return_value=(True, "san", "example.com", False),
            ),
            patch.object(
                dashboard_module,
                "certificate_info_from_dates",
                return_value=None,
            ),
        ):
            info = dashboard_module._fetch_remote_certificate_sync_for_target("example.com", "8.8.8.8")

        self.assertFalse(info.exists)
        self.assertEqual(info.status, "error")
        self.assertEqual(info.error_message, "Certificate validity dates could not be read.")
        self.assertIsNotNone(info.checked_at)

    async def test_resolve_public_certificate_target_returns_pinned_public_ip(self) -> None:
        loop = asyncio.get_running_loop()
        with patch.object(
            loop,
            "getaddrinfo",
            new=AsyncMock(
                return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443)),
                    (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2001:4860:4860::8888", 443, 0, 0)),
                ]
            ),
        ):
            domain, pinned_ip = await dashboard_module._resolve_public_certificate_target("example.com")

        self.assertEqual(domain, "example.com")
        self.assertEqual(pinned_ip, "8.8.8.8")

    async def test_fetch_remote_certificate_returns_error_on_dns_timeout(self) -> None:
        with patch.object(
            dashboard_module,
            "_resolve_public_certificate_target",
            side_effect=TimeoutError("DNS lookup timed out for this domain."),
        ):
            domain, info = await dashboard_module._fetch_remote_certificate("example.com")

        self.assertEqual(domain, "example.com")
        self.assertFalse(info.exists)
        self.assertEqual(info.status, "error")
        self.assertEqual(info.error_message, "Connection timed out while reading the certificate.")

    def test_apply_pending_status_preserves_existing_diagnostics(self) -> None:
        now = datetime.now(UTC)
        pending_source = dashboard_module.CertificateInfo(
            exists=False,
            valid=False,
            status="error",
            error_message="TLS handshake failed",
            checked_at=now - timedelta(minutes=5),
            diagnostics=("local_artifact_missing",),
            source="remote",
        )

        result = dashboard_module._apply_pending_status(
            {"example.com": pending_source},
            managed_site_states={"example.com": (True, now - timedelta(minutes=1))},
        )

        self.assertEqual(result["example.com"].status, "pending")
        self.assertEqual(
            result["example.com"].diagnostics,
            ("local_artifact_missing", "pending_after_site_update"),
        )
        self.assertEqual(result["example.com"].error_message, "TLS handshake failed")


if __name__ == "__main__":
    unittest.main()


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()
