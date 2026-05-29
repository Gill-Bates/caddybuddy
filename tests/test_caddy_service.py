#!/usr/bin/env python3
#
# tests/test_caddy_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
from ipaddress import ip_address
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.services.caddy import CaddyAdminClient, CaddyService, CaddyServiceError, _resolve_target_ips


class CaddyServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_validate_and_deploy_uses_normalized_admin_api_path(self) -> None:
        service = CaddyService()
        response = httpx.Response(200, request=httpx.Request("POST", "http://127.0.0.1:2019/load"))
        client = AsyncMock()
        client.post = AsyncMock(return_value=response)

        with (
            patch.object(service, "adapt_caddyfile_to_json", new=AsyncMock(return_value={"apps": {}})),
            patch.object(service, "_validate_settings_target", new=AsyncMock(return_value=ip_address("127.0.0.1"))),
            patch.object(service, "_get_client", return_value=client),
        ):
            ok, message = await service.validate_and_deploy_caddyfile(
                "example.com { respond \"ok\" }",
                api_url="http://localhost",
                api_port=2019,
                admin_api_path="/load",
            )

        self.assertTrue(ok)
        self.assertEqual(message, "Configuration deployed successfully")
        client.post.assert_awaited_once_with(
            "http://127.0.0.1:2019/load",
            json={"apps": {}},
            headers={"Host": "localhost:2019"},
        )

    async def test_validate_and_deploy_rejects_unsupported_admin_api_path(self) -> None:
        service = CaddyService()

        with patch.object(service, "adapt_caddyfile_to_json", new=AsyncMock(return_value={"apps": {}})):
            ok, message = await service.validate_and_deploy_caddyfile(
                "example.com { respond \"ok\" }",
                api_url="http://localhost",
                api_port=2019,
                admin_api_path="/config/",
            )

        self.assertFalse(ok)
        self.assertEqual(message, "Configuration error: Only the Caddy /load endpoint is supported.")

    async def test_validate_and_deploy_sanitizes_http_status_errors(self) -> None:
        service = CaddyService()
        request = httpx.Request("POST", "http://127.0.0.1:2019/load")
        response = httpx.Response(502, request=request)
        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.HTTPStatusError("boom", request=request, response=response))

        with (
            patch.object(service, "adapt_caddyfile_to_json", new=AsyncMock(return_value={"apps": {}})),
            patch.object(service, "_validate_settings_target", new=AsyncMock(return_value=ip_address("127.0.0.1"))),
            patch.object(service, "_get_client", return_value=client),
        ):
            ok, message = await service.validate_and_deploy_caddyfile(
                "example.com { respond \"ok\" }",
                api_url="http://localhost",
                api_port=2019,
                admin_api_path="/load",
            )

        self.assertFalse(ok)
        self.assertEqual(message, "Deployment failed with HTTP 502.")

    async def test_adapt_caddyfile_to_json_rejects_oversized_input(self) -> None:
        service = CaddyService()
        oversized = "x" * ((512 * 1024) + 1)

        with self.assertRaisesRegex(CaddyServiceError, "byte limit"):
            await service.adapt_caddyfile_to_json(oversized)

    async def test_resolve_target_ips_preserves_getaddrinfo_order(self) -> None:
        loop = AsyncMock()
        loop.getaddrinfo = AsyncMock(
            return_value=[
                (None, None, None, None, ("127.0.0.1", 2019)),
                (None, None, None, None, ("127.0.0.1", 2019)),
                (None, None, None, None, ("10.0.0.5", 2019)),
            ]
        )

        with patch("app.services.caddy.asyncio.get_running_loop", return_value=loop):
            resolved = await _resolve_target_ips("localhost", 2019)

        self.assertEqual(resolved, [ip_address("127.0.0.1"), ip_address("10.0.0.5")])

    async def test_admin_client_rejects_unapproved_hostnames(self) -> None:
        client = CaddyAdminClient("http://example.com:2019", 1.0)

        with self.assertRaisesRegex(CaddyServiceError, "not allowed"):
            await client.get_config()

    async def test_write_temp_caddyfile_uses_restrictive_permissions(self) -> None:
        path = await asyncio.to_thread(CaddyService._write_temp_caddyfile, "example.com { respond \"ok\" }")
        self.addAsyncCleanup(asyncio.to_thread, Path(path).unlink, True)

        mode = stat.S_IMODE(path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    async def test_purge_certificate_artifacts_does_not_delete_shared_parent_for_file_matches(self) -> None:
        service = CaddyService()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "certificates"
            ocsp_dir = root.parent / "ocsp"
            ocsp_dir.mkdir(parents=True)
            matched_file = ocsp_dir / "example.com.ocsp"
            sibling_file = ocsp_dir / "other.example.com.ocsp"
            matched_file.write_text("matched", encoding="utf-8")
            sibling_file.write_text("sibling", encoding="utf-8")

            removed = await service.purge_certificate_artifacts("example.com", root)

            self.assertEqual(removed, 1)
            self.assertFalse(matched_file.exists())
            self.assertTrue(sibling_file.exists())

    async def test_purge_certificate_artifacts_skips_inaccessible_roots(self) -> None:
        service = CaddyService()
        root = Path("/var/lib/caddy/.local/share/caddy/certificates")

        def fake_exists(path: Path) -> bool:
            if path == root:
                raise PermissionError("denied")
            return False

        with (
            patch("app.services.caddy.Path.exists", autospec=True, side_effect=fake_exists),
            patch("app.services.caddy.logger") as logger_mock,
        ):
            removed = await service.purge_certificate_artifacts("example.com", root)

        self.assertEqual(removed, 0)
        logger_mock.warning.assert_called_once_with(
            "Skipping inaccessible Caddy certificate storage root: %s",
            root,
        )

    async def test_purge_certificate_artifacts_aborts_when_scan_limit_is_exceeded(self) -> None:
        service = CaddyService()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "certificates"
            root.mkdir(parents=True)
            for index in range(4):
                (root / f"file-{index}.txt").write_text("x", encoding="utf-8")

            with patch("app.services.caddy._MAX_CERT_PURGE_SCAN_PATHS", 2):
                with self.assertRaisesRegex(CaddyServiceError, "scan limit exceeded"):
                    await service.purge_certificate_artifacts("example.com", root)


if __name__ == "__main__":
    unittest.main()