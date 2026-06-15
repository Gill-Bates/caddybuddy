#!/usr/bin/env python3
#
# tests/test_caddy_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from ipaddress import ip_address
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.services.caddy import CaddyAdminClient, CaddyService, CaddyServiceError, _resolve_target_ips


class CaddyServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_validate_caddyfile_returns_api_unavailable_on_non_caddy_error(self) -> None:
        service = CaddyService()

        with patch.object(
            service,
            "adapt_caddyfile_to_json",
            new=AsyncMock(side_effect=CaddyServiceError("leaked internal detail")),
        ):
            ok, message = await service.validate_caddyfile("example.com { respond \"ok\" }")

        self.assertFalse(ok)
        self.assertIn("admin URL", message)

    async def test_validate_caddyfile_surfaces_caddy_rejection_body(self) -> None:
        service = CaddyService()
        caddy_error = "unrecognized directive: foo"

        with patch.object(
            service,
            "adapt_caddyfile_to_json",
            new=AsyncMock(
                side_effect=CaddyServiceError(
                    f"Caddy Admin API request failed with status 400. Response body: {caddy_error}"
                )
            ),
        ):
            ok, message = await service.validate_caddyfile("example.com { foo }")

        self.assertFalse(ok)
        self.assertEqual(message, caddy_error)

    async def test_admin_client_includes_http_status_response_body(self) -> None:
        client = CaddyAdminClient("http://localhost:2019", 1.0)
        req = httpx.Request("POST", "http://127.0.0.1:2019/load")

        async def aiter_bytes():
            yield b"load config: invalid config"

        streamed = MagicMock()
        streamed.status_code = 400
        streamed.headers = {}
        streamed.aiter_bytes = aiter_bytes
        streamed.aclose = AsyncMock()

        http_client = MagicMock()
        http_client.build_request = MagicMock(return_value=req)
        http_client.send = AsyncMock(return_value=streamed)

        with (
            patch("app.services.caddy._resolve_target_ips", new=AsyncMock(return_value=[ip_address("127.0.0.1")])),
            patch.object(client, "_get_client", return_value=http_client),
        ):
            with self.assertRaisesRegex(CaddyServiceError, "status 400") as ctx:
                await client.load_config_force({"apps": {}}, force_reload=True)

        self.assertIn("invalid config", str(ctx.exception))

    async def test_adapt_caddyfile_to_json_rejects_oversized_input(self) -> None:
        service = CaddyService()
        oversized = "x" * ((512 * 1024) + 1)

        with self.assertRaisesRegex(CaddyServiceError, "byte limit"):
            await service.adapt_caddyfile_to_json(oversized)

    async def test_admin_client_adapt_caddyfile_posts_caddyfile_to_adapt_endpoint(self) -> None:
        client = CaddyAdminClient("http://localhost:2019", 1.0)
        response = httpx.Response(
            200,
            json={"result": {"apps": {}}},
            request=httpx.Request("POST", "http://127.0.0.1:2019/adapt"),
        )

        with patch.object(client, "_request", new=AsyncMock(return_value=response)) as request_mock:
            payload, warnings = await client.adapt_caddyfile('example.com { respond "ok" }')

        self.assertEqual(payload, {"apps": {}})
        self.assertEqual(warnings, [])
        request_mock.assert_awaited_once_with(
            "POST",
            "/adapt",
            content=b'example.com { respond "ok" }',
            headers={"Content-Type": "text/caddyfile"},
        )

    async def test_admin_client_adapt_caddyfile_unwraps_result_payload(self) -> None:
        client = CaddyAdminClient("http://localhost:2019", 1.0)
        response = httpx.Response(
            200,
            json={"result": {"apps": {"http": {}}}, "warnings": ["noop"]},
            request=httpx.Request("POST", "http://127.0.0.1:2019/adapt"),
        )

        with patch.object(client, "_request", new=AsyncMock(return_value=response)):
            payload, warnings = await client.adapt_caddyfile('example.com { respond "ok" }')

        self.assertEqual(payload, {"apps": {"http": {}}})
        self.assertEqual(warnings, ["noop"])

    async def test_service_adapt_caddyfile_to_json_delegates_to_admin_api(self) -> None:
        service = CaddyService()
        settings = SimpleNamespace(caddy_api_url="http://caddy:2019", caddy_admin_timeout_seconds=4.0)

        with (
            patch("app.services.caddy.get_settings", return_value=settings),
            patch("app.services.caddy.CaddyAdminClient") as client_cls,
        ):
            client = client_cls.return_value
            client.__aenter__.return_value = client
            client.adapt_caddyfile = AsyncMock(return_value=({"apps": {}}, []))

            payload = await service.adapt_caddyfile_to_json('example.com { respond "ok" }')

        self.assertEqual(payload, {"apps": {}})
        client_cls.assert_called_once_with("http://caddy:2019", 4.0)
        client.adapt_caddyfile.assert_awaited_once_with('example.com { respond "ok" }')

    async def test_format_caddyfile_uses_local_brace_indentation(self) -> None:
        service = CaddyService()

        formatted = await service.format_caddyfile(
            'example.com {\nrespond "ok"\nhandle /api/* {\nreverse_proxy app:8000\n}\n}'
        )

        self.assertEqual(
            formatted,
            'example.com {\n\trespond "ok"\n\thandle /api/* {\n\t\treverse_proxy app:8000\n\t}\n}\n',
        )

    async def test_format_caddyfile_does_not_indent_placeholders_as_blocks(self) -> None:
        service = CaddyService()

        formatted = await service.format_caddyfile(
            "example.com {\n"
            "reverse_proxy 10.30.0.12:3000 {\n"
            "header_up X-Real-IP {remote_host}\n"
            "header_up X-Forwarded-Port {port}\n"
            "}\n"
            "}\n"
        )

        self.assertEqual(
            formatted,
            "example.com {\n"
            "\treverse_proxy 10.30.0.12:3000 {\n"
            "\t\theader_up X-Real-IP {remote_host}\n"
            "\t\theader_up X-Forwarded-Port {port}\n"
            "\t}\n"
            "}\n",
        )

    async def test_format_site_directives_removes_dummy_site_wrapper(self) -> None:
        service = CaddyService()

        formatted = await service.format_site_directives(
            'handle /api/* {\nreverse_proxy app:8000\n}'
        )

        self.assertEqual(formatted, "handle /api/* {\n\treverse_proxy app:8000\n}")

    async def test_admin_client_adapt_caddyfile_rejects_non_object_json(self) -> None:
        client = CaddyAdminClient("http://localhost:2019", 1.0)
        response = httpx.Response(
            200,
            json=[],
            request=httpx.Request("POST", "http://127.0.0.1:2019/adapt"),
        )

        with patch.object(client, "_request", new=AsyncMock(return_value=response)):
            with self.assertRaisesRegex(CaddyServiceError, "JSON object"):
                await client.adapt_caddyfile('example.com { respond "ok" }')

    async def test_admin_client_adapt_caddyfile_reports_invalid_json(self) -> None:
        client = CaddyAdminClient("http://localhost:2019", 1.0)
        response = httpx.Response(
            200,
            content=b"not-json",
            request=httpx.Request("POST", "http://127.0.0.1:2019/adapt"),
        )

        with patch.object(client, "_request", new=AsyncMock(return_value=response)):
            with self.assertRaisesRegex(CaddyServiceError, "parse"):
                await client.adapt_caddyfile('example.com { respond "ok" }')

    async def test_admin_client_rejects_oversized_adapt_response_before_json_parsing(self) -> None:
        client = CaddyAdminClient("http://localhost:2019", 1.0)
        response = httpx.Response(
            200,
            content=b"x" * (2 * 1024 * 1024 + 1),
            request=httpx.Request("POST", "http://127.0.0.1:2019/adapt"),
        )

        with patch.object(client, "_request", new=AsyncMock(return_value=response)):
            with self.assertRaisesRegex(CaddyServiceError, "too large"):
                await client.adapt_caddyfile('example.com { respond "ok" }')

    async def test_admin_client_rejects_oversized_config_response_before_json_parsing(self) -> None:
        client = CaddyAdminClient("http://localhost:2019", 1.0)
        response = httpx.Response(
            200,
            content=b"x" * (2 * 1024 * 1024 + 1),
            request=httpx.Request("GET", "http://127.0.0.1:2019/config/"),
        )

        with patch.object(client, "_request", new=AsyncMock(return_value=response)):
            with self.assertRaisesRegex(CaddyServiceError, "too large"):
                await client.get_config()

    async def test_admin_client_summarizes_oversized_http_error_response(self) -> None:
        client = CaddyAdminClient("http://localhost:2019", 1.0)
        req = httpx.Request("POST", "http://127.0.0.1:2019/load")

        big_chunk = b"x" * (2 * 1024 * 1024 + 1)

        async def aiter_bytes():
            yield big_chunk

        streamed = MagicMock()
        streamed.status_code = 502
        streamed.headers = {}
        streamed.aiter_bytes = aiter_bytes
        streamed.aclose = AsyncMock()

        http_client = MagicMock()
        http_client.build_request = MagicMock(return_value=req)
        http_client.send = AsyncMock(return_value=streamed)

        with (
            patch("app.services.caddy._resolve_target_ips", new=AsyncMock(return_value=[ip_address("127.0.0.1")])),
            patch.object(client, "_get_client", return_value=http_client),
        ):
            with self.assertRaisesRegex(CaddyServiceError, "response body too large") as ctx:
                await client.load_config_force({"apps": {}}, force_reload=True)

        self.assertIn("status 502", str(ctx.exception))

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

    async def test_purge_certificate_artifacts_ignores_similar_domain_prefixes(self) -> None:
        service = CaddyService()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "certificates"
            target_dir = root / "acme-v02.api.letsencrypt.org-directory" / "example.com"
            similar_dir = root / "acme-v02.api.letsencrypt.org-directory" / "example.com-backup"
            ocsp_dir = root.parent / "ocsp"
            target_dir.mkdir(parents=True)
            similar_dir.mkdir(parents=True)
            ocsp_dir.mkdir(parents=True)
            (target_dir / "example.com.crt").write_text("target", encoding="utf-8")
            (similar_dir / "example.com-backup.crt").write_text("similar", encoding="utf-8")
            exact_ocsp = ocsp_dir / "example.com.ocsp"
            prefixed_ocsp = ocsp_dir / "example.com.backup.ocsp"
            exact_ocsp.write_text("ocsp", encoding="utf-8")
            prefixed_ocsp.write_text("backup", encoding="utf-8")

            removed = await service.purge_certificate_artifacts("example.com", root)

            self.assertEqual(removed, 2)
            self.assertFalse(target_dir.exists())
            self.assertFalse(exact_ocsp.exists())
            self.assertTrue(similar_dir.exists())
            self.assertTrue(prefixed_ocsp.exists())

    async def test_purge_certificate_artifacts_raises_for_unsafe_certificate_root(self) -> None:
        service = CaddyService()
        with tempfile.TemporaryDirectory() as temp_dir:
            unsafe_root = Path(temp_dir) / "not-certificates"
            unsafe_root.mkdir()

            with patch("app.services.caddy.logger") as logger_mock:
                with self.assertRaisesRegex(CaddyServiceError, "Unsafe Caddy certificate storage root"):
                    await service.purge_certificate_artifacts("example.com", unsafe_root)

        logger_mock.warning.assert_called_once_with(
            "Refusing certificate purge: unsafe configured root %s",
            unsafe_root,
        )

    async def test_purge_certificate_artifacts_does_not_delete_same_named_files_outside_scope_directory(self) -> None:
        service = CaddyService()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "certificates"
            active_dir = root / "acme-v02.api.letsencrypt.org-directory" / "example.com"
            archive_dir = root / "acme-v02.api.letsencrypt.org-directory" / "archive"
            ocsp_dir = root.parent / "ocsp"
            active_dir.mkdir(parents=True)
            archive_dir.mkdir(parents=True)
            ocsp_dir.mkdir(parents=True)
            (active_dir / "example.com.crt").write_text("active", encoding="utf-8")
            (archive_dir / "example.com.crt").write_text("archived", encoding="utf-8")
            (ocsp_dir / "example.com.ocsp").write_text("ocsp", encoding="utf-8")

            removed = await service.purge_certificate_artifacts("example.com", root)

            self.assertEqual(removed, 2)
            self.assertFalse(active_dir.exists())
            self.assertTrue(archive_dir.exists())
            self.assertTrue((archive_dir / "example.com.crt").exists())

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

    async def test_purge_certificate_artifacts_aborts_when_root_traversal_fails(self) -> None:
        service = CaddyService()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "certificates"
            root.mkdir(parents=True)

            with patch("app.services.caddy.Path.rglob", side_effect=OSError("simulated traversal failure")):
                with self.assertRaisesRegex(CaddyServiceError, "Could not scan certificate storage root"):
                    await service.purge_certificate_artifacts("example.com", root)

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
