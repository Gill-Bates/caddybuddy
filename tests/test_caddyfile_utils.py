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
    build_generated_site_block,
    directives_have_import,
    directives_have_log_block,
    directives_have_security_header_block,
    extract_site_handler_from_directives,
    extract_upstream_from_directives,
    parse_domain_directive_form_state,
    prepare_domain_directives,
    snippet_is_defined,
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


class SmartImportHelpersTests(unittest.TestCase):
    # --- snippet_is_defined ---

    def test_snippet_is_defined_finds_snippet(self) -> None:
        baseline = "(security_headers) {\n    header {\n        -Server\n    }\n}"
        self.assertTrue(snippet_is_defined(baseline, "security_headers"))

    def test_snippet_is_defined_returns_false_when_absent(self) -> None:
        self.assertFalse(snippet_is_defined("{ email admin@example.com }", "security_headers"))

    def test_snippet_is_defined_does_not_match_partial_name(self) -> None:
        baseline = "(security_headers_extra) {\n    header {}\n}"
        self.assertFalse(snippet_is_defined(baseline, "security_headers"))

    # --- directives_have_import ---

    def test_directives_have_import_detects_named_import(self) -> None:
        self.assertTrue(directives_have_import("import security_headers\nreverse_proxy app:8000", "security_headers"))

    def test_directives_have_import_returns_false_for_different_name(self) -> None:
        self.assertFalse(directives_have_import("import default_log\nreverse_proxy app:8000", "security_headers"))

    def test_directives_have_import_ignores_comments_and_case(self) -> None:
        directives = "# import security_headers\nImport security_headers\nreverse_proxy app:8000"
        self.assertTrue(directives_have_import(directives, "security_headers"))

    def test_directives_have_import_returns_false_when_none(self) -> None:
        self.assertFalse(directives_have_import(None, "security_headers"))

    # --- directives_have_log_block ---

    def test_directives_have_log_block_detects_log_block(self) -> None:
        self.assertTrue(directives_have_log_block("log {\n    output stdout\n}"))

    def test_directives_have_log_block_returns_false_without_log(self) -> None:
        self.assertFalse(directives_have_log_block("reverse_proxy app:8000"))

    def test_directives_have_log_block_ignores_commented_log_block(self) -> None:
        self.assertFalse(directives_have_log_block("# log {\n#     output stdout\n# }"))

    def test_directives_have_log_block_returns_false_when_none(self) -> None:
        self.assertFalse(directives_have_log_block(None))

    def test_directives_have_log_block_ignores_log_in_string(self) -> None:
        self.assertFalse(directives_have_log_block('respond "log { output stdout }"'))

    def test_directives_have_log_block_does_not_match_log_append(self) -> None:
        self.assertFalse(directives_have_log_block("log_append foo bar"))

    # --- directives_have_security_header_block ---

    def test_directives_have_security_header_block_detects_hsts(self) -> None:
        directives = 'header {\n    Strict-Transport-Security "max-age=31536000"\n}'
        self.assertTrue(directives_have_security_header_block(directives))

    def test_directives_have_security_header_block_detects_server_deletion(self) -> None:
        self.assertTrue(directives_have_security_header_block("header {\n    -Server\n}"))

    def test_directives_have_security_header_block_is_case_insensitive(self) -> None:
        directives = 'header {\n    strict-transport-security "max-age=31536000"\n}'
        self.assertTrue(directives_have_security_header_block(directives))

    def test_directives_have_security_header_block_ignores_commented_headers(self) -> None:
        directives = '# header {\n#     Strict-Transport-Security "max-age=31536000"\n# }'
        self.assertFalse(directives_have_security_header_block(directives))

    def test_directives_have_security_header_block_detects_inline_header(self) -> None:
        self.assertTrue(directives_have_security_header_block('header X-Frame-Options "DENY"'))

    def test_directives_have_security_header_block_false_for_non_security_header(self) -> None:
        self.assertFalse(directives_have_security_header_block("header {\n    X-Custom-App myapp\n}"))

    def test_directives_have_security_header_block_returns_false_when_none(self) -> None:
        self.assertFalse(directives_have_security_header_block(None))

    def test_directives_have_security_header_block_ignores_header_name_in_string(self) -> None:
        self.assertFalse(
            directives_have_security_header_block('respond "Strict-Transport-Security max-age=31536000"')
        )

    def test_directives_have_security_header_block_ignores_header_name_in_comment_after_code(self) -> None:
        self.assertFalse(
            directives_have_security_header_block("reverse_proxy app:8000 # Strict-Transport-Security")
        )

    def test_directives_have_security_header_block_detects_one_line_header_block(self) -> None:
        self.assertTrue(
            directives_have_security_header_block('header { Strict-Transport-Security "max-age=31536000" }')
        )

    # --- build_generated_site_block ---

    def test_build_generated_site_block_injects_both_imports(self) -> None:
        block = build_generated_site_block(
            name="example.com",
            upstream=None,
            caddy_directives="reverse_proxy app:8000",
            ssl_enabled=True,
            import_security_headers=True,
            import_default_log=True,
        )
        self.assertIn("import security_headers", block)
        self.assertIn("import default_log", block)
        self.assertIn("reverse_proxy app:8000", block)

    def test_build_generated_site_block_no_imports_when_false(self) -> None:
        block = build_generated_site_block(
            name="example.com",
            upstream=None,
            caddy_directives="reverse_proxy app:8000",
            ssl_enabled=True,
            import_security_headers=False,
            import_default_log=False,
        )
        self.assertNotIn("import", block)

    def test_build_generated_site_block_imports_appear_before_directives(self) -> None:
        block = build_generated_site_block(
            name="example.com",
            upstream=None,
            caddy_directives="reverse_proxy app:8000",
            ssl_enabled=True,
            import_security_headers=True,
            import_default_log=False,
        )
        import_pos = block.index("import security_headers")
        directive_pos = block.index("reverse_proxy")
        self.assertLess(import_pos, directive_pos)


if __name__ == "__main__":
    unittest.main()
