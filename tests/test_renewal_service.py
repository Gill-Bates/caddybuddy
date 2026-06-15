#!/usr/bin/env python3
#
# tests/test_renewal_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from datetime import datetime, UTC, timedelta
from types import SimpleNamespace

from app.models.entities import Site
from app.services.renewal import CertificateRenewalService, renewal_file_lock
from app.services.certificates import CertificateInfo, CertificateRenewalCapability, ParsedCertificate, CertificateName
from app.config.settings import get_settings

class RenewalServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = AsyncMock()
        self.service = CertificateRenewalService(self.session)
        self.settings = get_settings()

    def test_renewal_file_lock_concurrency(self) -> None:
        lock_dir = self.settings.data_dir / "locks"
        scope = "concurrency-test.com"
        
        # Acquire first lock
        with renewal_file_lock(lock_dir, scope):
            # Attempting to acquire second lock should raise RuntimeError
            with self.assertRaises(RuntimeError):
                with renewal_file_lock(lock_dir, scope):
                    pass

    async def test_verify_renewal_success_private_domain(self) -> None:
        mock_cert = ParsedCertificate(
            path=Path("/tmp/dummy.crt"),
            dns_names=(CertificateName(value="internal.local", source="san"),),
            issued_at=datetime.now(UTC) - timedelta(days=1),
            expires_at=datetime.now(UTC) + timedelta(days=30),
            local_artifact_complete=True
        )
        
        # Mock remote checks to return remote_check_unavailable for internal.local
        private_info = CertificateInfo(
            exists=False,
            valid=False,
            status="remote_check_unavailable"
        )
        
        with (
            patch("app.services.renewal.scan_certificate_storage", return_value=([mock_cert], False)),
            patch("app.services.renewal.get_certificate_info_for_domains_remote", new_callable=AsyncMock, return_value={"internal.local": private_info}) as remote_mock,
            patch("app.services.renewal.get_certificate_info_for_domains", new_callable=AsyncMock) as combined_mock,
            patch("app.services.renewal.invalidate_certificate_cache", new_callable=AsyncMock)
        ):
            success, msg = await self.service._verify_renewal_success(["internal.local"])
            self.assertTrue(success)
            self.assertIn("Certificate artifacts were recreated", msg)
            remote_mock.assert_awaited_once_with(["internal.local"])
            combined_mock.assert_not_awaited()

    async def test_execute_progress_callback(self) -> None:
        site = Site(domain="test.com", enabled=True)
        plan = CertificateRenewalCapability(
            mode="restart_repair",
            reason="local_artifact_missing",
            requires_confirmation=False,
            scope_name="test.com",
            scope_type="domain",
            wait_domains=("test.com",)
        )
        
        progress_mock = AsyncMock()
        
        with (
            patch.object(self.service, "_execute_restart_repair", new_callable=AsyncMock, return_value=(True, "recreated")) as execute_mock,
            patch("app.services.renewal.renewal_file_lock")
        ):
            success, msg = await self.service.execute(site, plan, progress=progress_mock)
            self.assertTrue(success)
            execute_mock.assert_called_once_with(["test.com"], progress_mock)

    async def test_execute_uses_plan_scope_for_locking(self) -> None:
        site = Site(domain="test.com", enabled=True)
        plan = CertificateRenewalCapability(
            mode="restart_repair",
            reason="local_artifact_missing",
            requires_confirmation=False,
            scope_name="shared-cert.example.com",
            scope_type="domain",
            wait_domains=("test.com",),
        )

        with (
            patch.object(self.service, "_execute_restart_repair", new=AsyncMock(return_value=(True, "recreated"))),
            patch("app.services.renewal.renewal_file_lock") as lock_mock,
        ):
            success, msg = await self.service.execute(site, plan)

        self.assertTrue(success)
        self.assertEqual(msg, "Caddy restarted and missing certificate artifacts were recreated successfully.")
        lock_mock.assert_called_once()
        self.assertEqual(lock_mock.call_args.args[1], "shared-cert.example.com")

    async def test_build_plan_limits_wait_domains_and_uses_artifact_scope(self) -> None:
        site = Site(domain="primary.example.com, secondary.example.com", enabled=True)
        cert_info = CertificateInfo(
            exists=True,
            valid=True,
            status="valid",
            source="local",
            local_artifact_present=True,
            local_artifact_complete=True,
            artifact_scope_name="shared-cert.example.com",
        )

        plan = await self.service._evaluate_renewal_capability(site, cert_info=cert_info, fetch_if_missing=False)

        self.assertEqual(plan.mode, "artifact_purge")
        self.assertEqual(plan.scope_name, "shared-cert.example.com")
        self.assertEqual(plan.wait_domains, ("primary.example.com",))

    async def test_build_plan_uses_restart_repair_for_incomplete_local_artifacts(self) -> None:
        site = Site(domain="mail.example.com", enabled=True)
        cert_info = CertificateInfo(
            exists=True,
            valid=True,
            status="valid",
            source="local",
            local_artifact_present=True,
            local_artifact_complete=False,
            artifact_scope_name="shared-cert.example.com",
        )

        plan = await self.service._evaluate_renewal_capability(site, cert_info=cert_info, fetch_if_missing=False)

        self.assertEqual(plan.mode, "restart_repair")
        self.assertEqual(plan.reason, "local_artifact_incomplete")
        self.assertEqual(plan.scope_name, "shared-cert.example.com")
        self.assertEqual(plan.wait_domains, ("mail.example.com",))

    async def test_build_plan_fetches_certificate_info_when_missing(self) -> None:
        site = Site(domain="mail.example.com", enabled=True)
        cert_info = CertificateInfo(
            exists=True,
            valid=True,
            status="valid",
            source="local",
            local_artifact_present=True,
            local_artifact_complete=True,
        )

        with patch(
            "app.services.renewal.get_certificate_info_for_domains",
            new=AsyncMock(return_value={"mail.example.com": cert_info}),
        ) as info_mock:
            plan = await self.service._evaluate_renewal_capability(site)

        info_mock.assert_awaited_once()
        self.assertEqual(plan.mode, "artifact_purge")
        self.assertEqual(plan.scope_name, "mail.example.com")

    async def test_build_plan_disables_forced_renewal_without_control_mode(self) -> None:
        site = Site(domain="mail.example.com", enabled=True)
        cert_info = CertificateInfo(
            exists=True,
            valid=True,
            status="valid",
            source="local",
            local_artifact_present=True,
            local_artifact_complete=True,
            artifact_scope_name="mail.example.com",
        )

        with patch.object(self.service.settings, "caddy_control_mode", "disabled"):
            plan = await self.service.build_plan(site, cert_info=cert_info, fetch_if_missing=False)

        self.assertEqual(plan.mode, "unavailable")
        self.assertEqual(plan.reason, "control_mode_disabled")
        self.assertEqual(plan.scope_name, "mail.example.com")
        self.assertEqual(plan.wait_domains, ("mail.example.com",))

    async def test_build_plan_keeps_artifact_purge_with_control_mode(self) -> None:
        site = Site(domain="mail.example.com", enabled=True)
        cert_info = CertificateInfo(
            exists=True,
            valid=True,
            status="valid",
            source="local",
            local_artifact_present=True,
            local_artifact_complete=True,
            artifact_scope_name="mail.example.com",
        )

        with patch.object(self.service.settings, "caddy_control_mode", "docker"):
            plan = await self.service.build_plan(site, cert_info=cert_info, fetch_if_missing=False)

        self.assertEqual(plan.mode, "artifact_purge")

    async def test_wait_for_local_artifacts_uses_certificate_coverage_match(self) -> None:
        wildcard_cert = ParsedCertificate(
            path=Path("/tmp/wildcard_.example.com/cert.crt"),
            dns_names=(CertificateName(value="*.example.com", source="san"),),
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC),
            local_artifact_complete=True,
        )

        with patch("app.services.renewal.scan_certificate_storage", return_value=([wildcard_cert], False)):
            success = await self.service._wait_for_local_artifacts(["grafana.example.com"], timeout=0.01)

        self.assertTrue(success)

    async def test_execute_artifact_purge_aborts_when_no_artifacts_removed(self) -> None:
        plan = CertificateRenewalCapability(
            mode="artifact_purge",
            reason="standard_renewal",
            requires_confirmation=False,
            scope_name="shared-cert.example.com",
            scope_type="domain",
            wait_domains=("mail.example.com",),
        )

        with (
            patch("app.services.renewal.caddy_service.purge_certificate_artifacts", new=AsyncMock(return_value=0)),
            patch(
                "app.services.renewal.get_caddy_supervisor",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        status=AsyncMock(return_value=SimpleNamespace(success=True, status="running", error=None)),
                        reload=AsyncMock(),
                        restart=AsyncMock(),
                    )
                ),
            ) as supervisor_mock,
            patch.object(self.service, "_wait_for_local_artifacts", new=AsyncMock()) as wait_mock,
        ):
            success, message = await self.service._execute_artifact_purge(["mail.example.com"], plan, None)

        self.assertFalse(success)
        self.assertEqual(message, "Forced renewal could not proceed: no matching local certificate artifacts were removed.")
        supervisor_mock.assert_awaited_once()
        wait_mock.assert_not_awaited()

    async def test_execute_artifact_purge_aborts_before_deleting_when_supervisor_disabled(self) -> None:
        plan = CertificateRenewalCapability(
            mode="artifact_purge",
            reason="standard_renewal",
            requires_confirmation=False,
            scope_name="shared-cert.example.com",
            scope_type="domain",
            wait_domains=("mail.example.com",),
        )
        supervisor = SimpleNamespace(
            status=AsyncMock(return_value=SimpleNamespace(success=True, status="disabled", error=None)),
            reload=AsyncMock(),
            restart=AsyncMock(),
        )

        with (
            patch("app.services.renewal.caddy_service.purge_certificate_artifacts", new=AsyncMock()) as purge_mock,
            patch("app.services.renewal.get_caddy_supervisor", new=AsyncMock(return_value=supervisor)),
        ):
            success, message = await self.service._execute_artifact_purge(["mail.example.com"], plan, None)

        self.assertFalse(success)
        self.assertIn("control mode", message)
        purge_mock.assert_not_awaited()
        supervisor.reload.assert_not_awaited()
        supervisor.restart.assert_not_awaited()

    async def test_execute_acquisition_sync_does_not_require_supervisor(self) -> None:
        site = Site(domain="test.com", enabled=True)
        plan = CertificateRenewalCapability(
            mode="acquisition_sync",
            reason="local_artifact_missing",
            requires_confirmation=False,
            scope_name="test.com",
            scope_type="domain",
            wait_domains=("test.com",),
        )

        with (
            patch("app.services.renewal.get_caddy_supervisor", new=AsyncMock()) as supervisor_mock,
            patch.object(self.service, "_verify_renewal_success", new=AsyncMock(return_value=(True, "ok"))),
            patch("app.services.renewal.renewal_file_lock"),
        ):
            success, message = await self.service.execute(site, plan)

        self.assertTrue(success)
        self.assertEqual(message, "Certificate was acquired successfully.")
        supervisor_mock.assert_not_awaited()

    async def test_artifact_purge_falls_back_to_restart_when_reload_does_not_recreate_artifacts(self) -> None:
        plan = CertificateRenewalCapability(
            mode="artifact_purge",
            reason="standard_renewal",
            requires_confirmation=False,
            scope_name="shared-cert.example.com",
            scope_type="domain",
            wait_domains=("mail.example.com",),
        )
        supervisor = SimpleNamespace(
            status=AsyncMock(return_value=SimpleNamespace(success=True, status="running", error=None)),
            reload=AsyncMock(return_value=SimpleNamespace(success=True, error=None)),
            restart=AsyncMock(return_value=SimpleNamespace(success=True, error=None)),
        )
        progress = AsyncMock()

        with (
            patch("app.services.renewal.caddy_service.purge_certificate_artifacts", new=AsyncMock(return_value=2)),
            patch("app.services.renewal.get_caddy_supervisor", new=AsyncMock(return_value=supervisor)),
            patch.object(self.service, "_wait_for_local_artifacts", new=AsyncMock(return_value=False)),
            patch.object(self.service, "_wait_for_caddy_health", new=AsyncMock(return_value=True)),
            patch.object(self.service, "_verify_renewal_success", new=AsyncMock(return_value=(True, "ok"))) as verify_mock,
        ):
            success, message = await self.service._execute_artifact_purge(["mail.example.com"], plan, progress)

        self.assertTrue(success)
        self.assertEqual(message, "ok")
        supervisor.reload.assert_awaited_once()
        supervisor.restart.assert_awaited_once()
        verify_mock.assert_awaited_once_with(["mail.example.com"])
        progress.assert_any_await("waiting_for_certificate", {})
        progress.assert_any_await("restarting_caddy", {})
