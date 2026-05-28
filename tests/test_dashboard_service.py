#!/usr/bin/env python3
#
# tests/test_dashboard_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import socket
import ssl
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app.services.dashboard as dashboard_module


class DashboardServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async with dashboard_module._cert_cache_lock:
            dashboard_module._cert_cache.clear()

    async def asyncTearDown(self) -> None:
        async with dashboard_module._cert_cache_lock:
            dashboard_module._cert_cache.clear()

    async def test_get_dashboard_metrics_aggregates_domains_and_certificate_counts(self) -> None:
        sites = [
            SimpleNamespace(domain="valid.example.com", enabled=True),
            SimpleNamespace(domain="expired.example.com", enabled=False),
        ]

        with (
            patch.object(dashboard_module.site_repository, "list_all", new=AsyncMock(return_value=sites)),
            patch.object(
                dashboard_module,
                "get_certificate_info_for_domains_remote",
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
                "get_certificate_info_for_domains_remote",
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
                "get_certificate_info_for_domains_remote",
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
                "get_certificate_info_for_domains_remote",
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

    def test_scan_certificate_counts_returns_zero_for_missing_path(self) -> None:
        valid_count, expired_count = dashboard_module._scan_certificate_counts(Path("/does/not/exist"))

        self.assertEqual(valid_count, 0)
        self.assertEqual(expired_count, 0)

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
        cached_info = dashboard_module.CertificateInfo(exists=True, valid=True, days_remaining=1)

        async with dashboard_module._cert_cache_lock:
            dashboard_module._cert_cache["example.com"] = (datetime.now(UTC), cached_info)

        with patch.object(dashboard_module, "_fetch_remote_certificate", new=AsyncMock()) as fetch_remote_mock:
            result = await dashboard_module.get_certificate_info_for_domains(["example.com"])

        fetch_remote_mock.assert_not_awaited()
        self.assertEqual(result, {"example.com": cached_info})

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

    def test_fetch_remote_certificate_sync_returns_error_message_and_logs_at_error(self) -> None:
        with (
            patch.object(
                dashboard_module.socket,
                "create_connection",
                side_effect=ssl.SSLError("[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error (_ssl.c:1029)"),
            ),
            self.assertLogs(dashboard_module.logger, level="ERROR") as log_capture,
        ):
            info = dashboard_module._fetch_remote_certificate_sync("example.com")

        self.assertFalse(info.exists)
        self.assertEqual(
            info.error_message,
            "TLS handshake failed: the remote server aborted the connection with an internal TLS error.",
        )
        self.assertTrue(any("Failed to fetch certificate for example.com" in entry for entry in log_capture.output))

    def test_fetch_remote_certificate_sync_blocks_non_public_targets_before_connecting(self) -> None:
        with (
            patch.object(
                dashboard_module.socket,
                "getaddrinfo",
                return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
                ],
            ),
            patch.object(dashboard_module.socket, "create_connection") as create_connection_mock,
        ):
            info = dashboard_module._fetch_remote_certificate_sync("dashboard.example.com")

        create_connection_mock.assert_not_called()
        self.assertFalse(info.exists)
        self.assertEqual(info.error_message, "Certificate checks require a public hostname.")


if __name__ == "__main__":
    unittest.main()