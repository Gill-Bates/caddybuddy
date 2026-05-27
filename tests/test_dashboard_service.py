#!/usr/bin/env python3
#
# tests/test_dashboard_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app.services.dashboard as dashboard_module


class DashboardServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_dashboard_metrics_aggregates_domains_and_certificate_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            certificates_path = Path(temp_dir)
            valid_certificate = certificates_path / "valid.crt"
            expired_certificate = certificates_path / "expired.crt"
            ignored_file = certificates_path / "ignored.txt"
            valid_certificate.write_text("valid", encoding="utf-8")
            expired_certificate.write_text("expired", encoding="utf-8")
            ignored_file.write_text("ignored", encoding="utf-8")

            now = datetime.now(UTC)

            with (
                patch.object(dashboard_module.site_repository, "count", new=AsyncMock(return_value=7)),
                patch.object(
                    dashboard_module,
                    "get_settings",
                    return_value=SimpleNamespace(
                        caddy_certificates_path=certificates_path,
                        caddy_admin_url="http://localhost:2019",
                        caddy_admin_timeout_seconds=10.0,
                    ),
                ),
                patch.object(
                    dashboard_module,
                    "_decode_certificate_expiry",
                    side_effect=lambda path: {
                        valid_certificate: now + timedelta(days=10),
                        expired_certificate: now - timedelta(days=2),
                    }.get(path),
                ),
                patch.object(
                    dashboard_module,
                    "_get_caddy_service_metrics",
                    return_value=dashboard_module.HostServiceMetrics(status="Running", uptime="2h 15m", version="v2.8.4"),
                ),
            ):
                metrics = await dashboard_module.get_dashboard_metrics(SimpleNamespace())

        self.assertEqual(metrics.domain_count, 7)
        self.assertEqual(metrics.valid_certificate_count, 1)
        self.assertEqual(metrics.expired_certificate_count, 1)
        self.assertEqual(metrics.caddy_service_status, "Running")
        self.assertEqual(metrics.caddy_service_uptime, "2h 15m")
        self.assertEqual(metrics.caddy_version, "v2.8.4")

    async def test_get_dashboard_metrics_keeps_unknown_status_when_api_version_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            certificates_path = Path(temp_dir)

            with (
                patch.object(dashboard_module.site_repository, "count", new=AsyncMock(return_value=0)),
                patch.object(
                    dashboard_module,
                    "get_settings",
                    return_value=SimpleNamespace(
                        caddy_certificates_path=certificates_path,
                        caddy_admin_url="http://localhost:2019",
                        caddy_admin_timeout_seconds=10.0,
                    ),
                ),
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

    def test_scan_certificate_counts_returns_zero_for_missing_path(self) -> None:
        valid_count, expired_count = dashboard_module._scan_certificate_counts(Path("/does/not/exist"))

        self.assertEqual(valid_count, 0)
        self.assertEqual(expired_count, 0)

    async def test_get_caddy_service_metrics_returns_running_when_admin_api_is_healthy(self) -> None:
        fake_client = AsyncMock()
        fake_client.health.return_value = True
        fake_client.__aenter__.return_value = fake_client
        fake_client.__aexit__.return_value = None

        with (
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
            metrics = await dashboard_module._get_caddy_service_metrics()

        client_cls.assert_called_once_with("http://localhost:2019", timeout_seconds=2.0)
        self.assertEqual(metrics.status, "Running")
        self.assertEqual(metrics.uptime, "Unavailable")
        self.assertEqual(metrics.version, "Unavailable")

    async def test_get_caddy_service_metrics_returns_unknown_when_admin_api_is_unreachable(self) -> None:
        fake_client = AsyncMock()
        fake_client.health.side_effect = dashboard_module.CaddyServiceError("boom")
        fake_client.__aenter__.return_value = fake_client
        fake_client.__aexit__.return_value = None

        with (
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
            metrics = await dashboard_module._get_caddy_service_metrics()

        self.assertEqual(metrics.status, "Unknown")
        self.assertEqual(metrics.uptime, "Unavailable")
        self.assertEqual(metrics.version, "Unavailable")

    async def test_get_caddy_service_metrics_uses_one_client_for_health_and_version(self) -> None:
        fake_client = AsyncMock()
        fake_client.health.return_value = True
        fake_client.__aenter__.return_value = fake_client
        fake_client.__aexit__.return_value = None

        with (
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
            metrics = await dashboard_module._get_caddy_service_metrics()

        fake_client.health.assert_awaited_once()
        fake_client.get_version.assert_not_called()
        self.assertEqual(metrics.version, "Unavailable")

    def test_get_certificate_info_for_domains_scans_certificate_tree_once(self) -> None:
        certificate_index = {
            "example.com": dashboard_module.CertificateInfo(exists=True, valid=True, days_remaining=1),
            "www.example.com": dashboard_module.CertificateInfo(exists=True, valid=True, days_remaining=5),
        }

        with (
            patch.object(
                dashboard_module,
                "get_settings",
                return_value=SimpleNamespace(caddy_certificates_path=Path("/certs")),
            ),
            patch.object(
                dashboard_module,
                "_build_certificate_index",
                return_value=certificate_index,
            ) as build_index_mock,
        ):
            result = dashboard_module.get_certificate_info_for_domains(["example.com", "www.example.com"])

        build_index_mock.assert_called_once_with(Path("/certs"))
        self.assertTrue(result["example.com"].valid)
        self.assertEqual(result["example.com"].days_remaining, 1)
        self.assertEqual(result["www.example.com"].days_remaining, 5)


if __name__ == "__main__":
    unittest.main()