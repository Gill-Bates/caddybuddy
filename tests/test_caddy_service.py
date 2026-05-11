#!/usr/bin/env python3
#
# tests/test_caddy_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
from ipaddress import ip_address
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.services.caddy import CaddyService, CaddyServiceError, caddy_service


class CaddyServiceImportTests(unittest.TestCase):
    def test_extract_site_definitions_imports_bootstrap_route(self) -> None:
        payload = {
            "logging": {
                "logs": {
                    "log0": {
                        "writer": {
                            "filename": "/var/log/caddy/access.log",
                            "output": "file",
                            "roll_size_mb": 10,
                        },
                        "encoder": {"format": "json"},
                    }
                }
            },
            "apps": {
                "http": {
                    "servers": {
                        "srv0": {
                            "listen": [":443"],
                            "logs": {
                                "logger_names": {
                                    "caddy.sv2.cirrio.de": ["log0"],
                                }
                            },
                            "routes": [
                                {
                                    "match": [{"host": ["caddy.sv2.cirrio.de"]}],
                                    "handle": [
                                        {
                                            "handler": "subroute",
                                            "routes": [
                                                {
                                                    "handle": [
                                                        {
                                                            "handler": "headers",
                                                            "response": {
                                                                "set": {
                                                                    "Strict-Transport-Security": ["max-age=31536000; includeSubDomains; preload"],
                                                                    "X-Content-Type-Options": ["nosniff"],
                                                                    "X-Frame-Options": ["DENY"],
                                                                    "Referrer-Policy": ["strict-origin-when-cross-origin"],
                                                                }
                                                            },
                                                        },
                                                        {
                                                            "handler": "headers",
                                                            "response": {
                                                                "deferred": True,
                                                                "delete": ["Server", "X-Powered-By"],
                                                            },
                                                        },
                                                        {"handler": "request_body", "max_size": 100000000},
                                                        {
                                                            "handler": "encode",
                                                            "encodings": {"gzip": {}},
                                                            "prefer": ["gzip"],
                                                        },
                                                        {
                                                            "handler": "reverse_proxy",
                                                            "headers": {
                                                                "request": {
                                                                    "set": {
                                                                        "Host": ["{http.request.host}"],
                                                                        "Authorization": ["{http.request.header.Authorization}"],
                                                                    }
                                                                }
                                                            },
                                                            "transport": {
                                                                "protocol": "http",
                                                                "keep_alive": {"idle_timeout": 30000000000},
                                                            },
                                                            "upstreams": [{"dial": "10.30.0.140:8000"}],
                                                        },
                                                    ]
                                                }
                                            ],
                                        }
                                    ],
                                    "terminal": True,
                                }
                            ],
                        }
                    }
                }
            },
        }

        imported = caddy_service.extract_site_definitions(payload, template_name_prefix="sv2")

        self.assertEqual(len(imported), 1)
        site = imported[0]
        self.assertEqual(site.domain, "caddy.sv2.cirrio.de")
        self.assertEqual(site.template_name, "sv2 (caddy.sv2.cirrio.de)")
        self.assertEqual(site.upstream, "10.30.0.140:8000")
        self.assertTrue(site.ssl_enabled)
        self.assertIn("import security_headers", site.caddyfile)
        self.assertIn("import default_log", site.caddyfile)
        self.assertIn("reverse_proxy {{upstream}}", site.caddyfile)
        self.assertIn("header_up Authorization {http.request.header.Authorization}", site.caddyfile)
        self.assertIn("max_size 100MB", site.caddyfile)

    def test_base_url_rejects_missing_or_invalid_api_port(self) -> None:
        for invalid_port in (None, "2019", 0, 65536):
            server = SimpleNamespace(
                api_url="http://127.0.0.1",
                api_port=invalid_port,
            )

            with self.assertRaisesRegex(ValueError, "api_port must be a valid integer port"):
                caddy_service._base_url(server)

    def test_relative_admin_api_path_rejects_non_string_values(self) -> None:
        server = SimpleNamespace(admin_api_path=None)

        with self.assertRaisesRegex(ValueError, "admin_api_path must be a string"):
            caddy_service._relative_admin_api_path(server)


class CaddyServiceTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_config_pins_request_to_validated_ip_with_host_header(self) -> None:
        service = CaddyService()
        server = SimpleNamespace(api_url="http://admin.example.com", api_port=2019, admin_api_path="config/")
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"status": "ok"},
        )
        client = SimpleNamespace(get=AsyncMock(return_value=response))

        with (
            patch.object(service, "_validate_target", new=AsyncMock(return_value=ip_address("203.0.113.10"))),
            patch.object(service, "_get_client", return_value=client),
        ):
            payload = await service.fetch_config(server)

        self.assertEqual(payload, {"status": "ok"})
        client.get.assert_awaited_once_with(
            "http://203.0.113.10:2019/config/",
            headers={"Host": "admin.example.com"},
        )

    async def test_deploy_config_pins_request_to_validated_ip_with_host_header(self) -> None:
        service = CaddyService()
        server = SimpleNamespace(api_url="http://admin.example.com", api_port=2019, admin_api_path="config/")
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            content=b'{"status":"ok"}',
            json=lambda: {"status": "ok"},
        )
        client = SimpleNamespace(post=AsyncMock(return_value=response))

        with (
            patch.object(service, "_validate_target", new=AsyncMock(return_value=ip_address("203.0.113.10"))),
            patch.object(service, "_get_client", return_value=client),
        ):
            payload = await service.deploy_config(server, {"apps": {}})

        self.assertEqual(payload, {"status": "ok"})
        client.post.assert_awaited_once_with(
            "http://203.0.113.10:2019/load",
            json={"apps": {}},
            headers={"Host": "admin.example.com"},
        )

    async def test_resolve_target_ips_times_out_dns_lookup(self) -> None:
        service = CaddyService()

        class _Loop:
            def getaddrinfo(self, *args, **kwargs):
                return asyncio.Future()

        with patch("app.services.caddy.asyncio.get_running_loop", return_value=_Loop()):
            with self.assertRaisesRegex(ValueError, "DNS resolution timed out"):
                await service._resolve_target_ips("admin.example.com", 2019)

    async def test_run_caddy_command_kills_process_on_cancellation(self) -> None:
        service = CaddyService()
        process = SimpleNamespace(communicate=AsyncMock(side_effect=asyncio.CancelledError()), returncode=0)

        with (
            patch.object(service, "_find_caddy_binary", return_value="/usr/bin/caddy"),
            patch("app.services.caddy.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)),
            patch.object(service, "_kill_process", new=AsyncMock()) as kill_process,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await service._run_caddy_command(["adapt"])

        kill_process.assert_awaited_once_with(process)

    async def test_adapt_caddyfile_to_json_unlinks_tempfile_via_thread(self) -> None:
        service = CaddyService()
        tmp_path = Path("/tmp/test.caddyfile")

        async def _fake_to_thread(func, *args, **kwargs):
            if getattr(func, "__name__", "") == "unlink":
                return None
            return func(*args, **kwargs)

        with (
            patch("app.services.caddy.asyncio.to_thread", new=AsyncMock(side_effect=_fake_to_thread)) as to_thread,
            patch.object(service, "_write_temp_caddyfile", return_value=tmp_path),
            patch.object(service, "_run_caddy_command", new=AsyncMock(return_value=(0, '{"apps": {}}', ""))),
        ):
            payload = await service.adapt_caddyfile_to_json("example.com { respond ok }")

        self.assertEqual(payload, {"apps": {}})
        self.assertGreaterEqual(to_thread.await_count, 2)

    async def test_fetch_config_rejects_non_json_response(self) -> None:
        service = CaddyService()
        server = SimpleNamespace(api_url="http://admin.example.com", api_port=2019, admin_api_path="config/")
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: (_ for _ in ()).throw(ValueError("bad json")),
        )
        client = SimpleNamespace(get=AsyncMock(return_value=response))

        with (
            patch.object(service, "_validate_target", new=AsyncMock(return_value=ip_address("203.0.113.10"))),
            patch.object(service, "_get_client", return_value=client),
        ):
            with self.assertRaisesRegex(CaddyServiceError, "Non-JSON response from Caddy API"):
                await service.fetch_config(server)


if __name__ == "__main__":
    unittest.main()