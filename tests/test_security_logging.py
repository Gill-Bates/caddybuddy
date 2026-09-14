#!/usr/bin/env python3
#
# tests/test_security_logging.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.utils.security_logging import log_authentication_failure


class SecurityLoggingTests(unittest.TestCase):
    def test_compose_example_does_not_trust_every_forwarded_client(self) -> None:
        compose = Path("docker/docker-compose.yml.example").read_text(encoding="utf-8")

        self.assertIn("FORWARDED_ALLOW_IPS: ${FORWARDED_ALLOW_IPS:-127.0.0.1}", compose)
        self.assertNotIn('FORWARDED_ALLOW_IPS: "*"', compose)

    @staticmethod
    def _app() -> FastAPI:
        app = FastAPI()

        @app.get("/")
        async def emit_failure(request: Request) -> dict[str, bool]:
            log_authentication_failure(
                request,
                username="admin",
                reason="invalid_credentials",
                status_code=403,
            )
            return {"ok": True}

        return app

    def _logged_message(
        self,
        *,
        trusted_hosts: list[str],
        client_host: str,
        forwarded_for: str,
    ) -> str:
        app = ProxyHeadersMiddleware(self._app(), trusted_hosts=trusted_hosts)
        with (
            self.assertLogs("caddybuddy.security", level="WARNING") as captured,
            TestClient(app, client=(client_host, 50000)) as client,
        ):
            response = client.get("/", headers={"X-Forwarded-For": forwarded_for})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured.records), 1)
        return captured.records[0].getMessage()

    def test_trusted_proxy_chain_reports_the_original_ipv4_client(self) -> None:
        message = self._logged_message(
            trusted_hosts=["10.0.0.0/8"],
            client_host="10.0.0.2",
            forwarded_for="198.51.100.27, 10.0.0.3",
        )

        self.assertEqual(
            message,
            'SECURITY authentication_failed client_ip=198.51.100.27 username="admin" '
            "reason=invalid_credentials status_code=403",
        )

    def test_trusted_proxy_reports_a_normalized_ipv6_client(self) -> None:
        message = self._logged_message(
            trusted_hosts=["10.0.0.0/8"],
            client_host="10.0.0.2",
            forwarded_for="2001:0db8:0000:0000:0000:0000:0000:0001",
        )

        self.assertIn("client_ip=2001:db8::1 ", message)

    def test_untrusted_client_cannot_spoof_forwarded_for(self) -> None:
        message = self._logged_message(
            trusted_hosts=["10.0.0.0/8"],
            client_host="203.0.113.8",
            forwarded_for="198.51.100.99",
        )

        self.assertIn("client_ip=203.0.113.8 ", message)
        self.assertNotIn("198.51.100.99", message)

    def test_malformed_client_and_username_cannot_inject_log_lines(self) -> None:
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "path": "/login",
                "raw_path": b"/login",
                "query_string": b"",
                "headers": [],
                "scheme": "https",
                "server": ("testserver", 443),
                "client": ("invalid\nclient_ip=192.0.2.44", 12345),
                "root_path": "",
                "app": FastAPI(),
            }
        )

        with self.assertLogs("caddybuddy.security", level="WARNING") as captured:
            log_authentication_failure(
                request,
                username="admin\nSECURITY authentication_failed client_ip=192.0.2.55",
                reason="invalid_credentials",
                status_code=403,
            )

        message = captured.records[0].getMessage()
        self.assertIn("client_ip=unknown", message)
        self.assertIn(
            r'username="admin\nSECURITY authentication_failed client_ip=192.0.2.55"',
            message,
        )
        self.assertEqual(len(message.splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
