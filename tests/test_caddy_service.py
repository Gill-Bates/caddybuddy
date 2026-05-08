#!/usr/bin/env python3
#
# tests/test_caddy_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.caddy import caddy_service


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


if __name__ == "__main__":
    unittest.main()