#!/usr/bin/env python3
#
# tests/test_caddyfile_utils.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest

from app.utils.caddyfile import (
    DomainDirectiveFormState,
    build_domain_directives,
    build_domain_site_preview,
    extract_site_handler_from_directives,
    extract_upstream_from_directives,
    parse_domain_directive_form_state,
    prepare_domain_directives,
)


class CaddyfileUtilsTests(unittest.TestCase):
    def test_build_domain_directives_rejects_upstream_injection(self) -> None:
        with self.assertRaisesRegex(ValueError, "Upstream must be a single Caddy token"):
            build_domain_directives(
                upstream="127.0.0.1:8000\nheader {\n    X-Test injected\n}",
                reverse_proxy_options=None,
                encode_directives=None,
                header_directives=None,
                request_body_directives=None,
                log_directives=None,
                tls_directives=None,
                basic_auth_directives=None,
                custom_directives=None,
            )

    def test_prepare_domain_directives_reports_upstream_injection(self) -> None:
        result = prepare_domain_directives(
            upstream="127.0.0.1:8000\nheader {\n    X-Test injected\n}",
            reverse_proxy_options=None,
            encode_directives=None,
            header_directives=None,
            request_body_directives=None,
            log_directives=None,
            tls_directives=None,
            basic_auth_directives=None,
            custom_directives=None,
        )

        self.assertIn("Upstream must be a single Caddy token", result.errors)
        self.assertIsNone(result.caddy_directives)

    def test_build_domain_site_preview_rejects_site_label_injection(self) -> None:
        with self.assertRaisesRegex(ValueError, "site label must not contain newlines or braces"):
            build_domain_site_preview(
                name="example.com\nheader {",
                upstream="127.0.0.1:8000",
                caddy_directives=None,
                ssl_enabled=True,
            )

    def test_prepare_domain_directives_blocks_denied_custom_directive(self) -> None:
        result = prepare_domain_directives(
            upstream="127.0.0.1:8000",
            reverse_proxy_options=None,
            encode_directives=None,
            header_directives=None,
            request_body_directives=None,
            log_directives=None,
            tls_directives=None,
            basic_auth_directives=None,
            custom_directives="import /etc/caddy/*",
        )

        self.assertIn("Custom directive 'import' is not allowed.", result.errors)

    def test_parse_domain_directive_form_state_keeps_multi_upstream_block_custom(self) -> None:
        state = parse_domain_directive_form_state(
            "reverse_proxy app1:8000 app2:8000 {\n    lb_policy round_robin\n}"
        )

        self.assertEqual(state, DomainDirectiveFormState(custom_directives="reverse_proxy app1:8000 app2:8000 {\n    lb_policy round_robin\n}"))

    def test_extract_upstream_from_directives_ignores_multi_upstream_block(self) -> None:
        self.assertIsNone(
            extract_upstream_from_directives(
                "reverse_proxy app1:8000 app2:8000 {\n    lb_policy round_robin\n}"
            )
        )

    def test_extract_upstream_from_directives_returns_single_upstream(self) -> None:
        self.assertEqual(
            extract_upstream_from_directives("reverse_proxy app1:8000 {\n    health_uri /health\n}"),
            "app1:8000",
        )

    def test_extract_site_handler_from_directives_returns_first_supported_handler(self) -> None:
        self.assertEqual(
            extract_site_handler_from_directives(
                "import security_headers\nheader {\n    X-Test value\n}\nreverse_proxy app1:8000"
            ),
            "reverse_proxy",
        )

    def test_extract_site_handler_from_directives_returns_none_without_supported_handler(self) -> None:
        self.assertIsNone(
            extract_site_handler_from_directives(
                "import security_headers\nheader {\n    X-Test value\n}\nencode gzip"
            )
        )

    def test_prepare_domain_directives_reports_unmatched_closing_brace(self) -> None:
        result = prepare_domain_directives(
            upstream="127.0.0.1:8000",
            reverse_proxy_options=None,
            encode_directives=None,
            header_directives=None,
            request_body_directives=None,
            log_directives=None,
            tls_directives=None,
            basic_auth_directives=None,
            custom_directives="respond ok\n}",
        )

        self.assertIn("Caddy directives contain an unmatched closing brace.", result.errors)

    def test_build_domain_site_preview_ignores_invalid_upstream_token(self) -> None:
        preview = build_domain_site_preview(
            name="example.com",
            upstream="127.0.0.1:8000\nheader {",
            caddy_directives=None,
            ssl_enabled=True,
        )

        self.assertIn("# Add Caddy directives here", preview)
        self.assertNotIn("reverse_proxy 127.0.0.1:8000", preview)

    def test_build_domain_site_preview_supports_multiple_domains(self) -> None:
        preview = build_domain_site_preview(
            name="Example.com www.example.com, example.com",
            upstream="127.0.0.1:8000",
            caddy_directives=None,
            ssl_enabled=True,
        )

        self.assertTrue(preview.startswith("example.com, www.example.com {"))


if __name__ == "__main__":
    unittest.main()