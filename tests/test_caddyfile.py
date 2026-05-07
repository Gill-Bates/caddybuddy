#!/usr/bin/env python3

import unittest

from app.utils.caddyfile import (
    DomainDirectiveFormState,
    build_domain_site_preview,
    prepare_domain_directives,
    extract_upstream_from_directives,
    normalize_caddy_directives,
    parse_domain_directive_form_state,
)


class CaddyfileUtilsTests(unittest.TestCase):
    def test_extract_upstream_ignores_block_only_reverse_proxy(self) -> None:
        directives = "reverse_proxy {\n    to 10.0.0.1:8080\n}"

        self.assertIsNone(extract_upstream_from_directives(directives))

    def test_extract_upstream_accepts_indented_reverse_proxy(self) -> None:
        directives = "    reverse_proxy 10.0.0.1:8080"

        self.assertEqual(
            extract_upstream_from_directives(directives),
            "10.0.0.1:8080",
        )

    def test_normalize_caddy_directives_unwraps_site_block_with_trailing_comment(self) -> None:
        directives = "example.com {\n    reverse_proxy 10.0.0.1:8080\n} # end"

        self.assertEqual(
            normalize_caddy_directives(directives),
            "reverse_proxy 10.0.0.1:8080",
        )

    def test_normalize_caddy_directives_preserves_inner_directive_blocks(self) -> None:
        directives = (
            "reverse_proxy 10.0.0.1:8080 {\n"
            "    header_up Host {host}\n"
            "}\n\n"
            "encode gzip"
        )

        self.assertEqual(normalize_caddy_directives(directives), directives)

    def test_build_domain_site_preview_preserves_nested_indentation(self) -> None:
        preview = build_domain_site_preview(
            name="example.com",
            upstream=None,
            caddy_directives="header {\n    -Server\n}",
            ssl_enabled=True,
        )

        self.assertEqual(
            preview,
            "example.com {\n"
            "    header {\n"
            "        -Server\n"
            "    }\n"
            "}",
        )

    def test_parse_domain_directive_form_state_extracts_additional_structured_blocks(self) -> None:
        directives = (
            "basic_auth {\n"
            "    alice $2a$14$example\n"
            "}\n\n"
            "reverse_proxy backend:8080\n\n"
            "encode zstd gzip\n\n"
            "tls {\n"
            "    issuer acme\n"
            "}"
        )

        state = parse_domain_directive_form_state(directives)

        self.assertEqual(
            state,
            DomainDirectiveFormState(
                upstream="backend:8080",
                reverse_proxy_options="",
                encode_directives="zstd gzip",
                header_directives="",
                request_body_directives="",
                log_directives="",
                tls_directives="issuer acme",
                basic_auth_directives="alice $2a$14$example",
                custom_directives="",
            ),
        )

    def test_prepare_domain_directives_builds_structured_blocks(self) -> None:
        result = prepare_domain_directives(
            upstream="backend:8080",
            reverse_proxy_options="",
            encode_directives="zstd gzip",
            header_directives="Strict-Transport-Security \"max-age=31536000\"",
            request_body_directives="",
            log_directives="output stdout",
            tls_directives="issuer acme",
            basic_auth_directives="alice $2a$14$example",
            custom_directives="respond /healthz 200",
        )

        self.assertEqual(result.errors, ())
        self.assertEqual(result.upstream, "backend:8080")
        self.assertEqual(
            result.caddy_directives,
            "basic_auth {\n"
            "alice $2a$14$example\n"
            "}\n\n"
            "reverse_proxy backend:8080\n\n"
            "encode zstd gzip\n\n"
            "header {\n"
            "Strict-Transport-Security \"max-age=31536000\"\n"
            "}\n\n"
            "log {\n"
            "output stdout\n"
            "}\n\n"
            "tls {\n"
            "issuer acme\n"
            "}\n\n"
            "respond /healthz 200",
        )

    def test_prepare_domain_directives_validates_missing_upstream_and_unbalanced_blocks(self) -> None:
        result = prepare_domain_directives(
            upstream=None,
            reverse_proxy_options="transport http {\n    keepalive 30s",
            encode_directives="encode { gzip }",
            header_directives="header {\n    -Server\n}",
            request_body_directives="",
            log_directives="",
            tls_directives="issuer acme",
            basic_auth_directives="",
            custom_directives="route {\n    respond \"ok\"",
        )

        self.assertEqual(
            result.errors,
            (
                "Reverse proxy options require an upstream target.",
                "Reverse proxy options contains unbalanced braces.",
                "Header block expects only the inner directives, not the outer header wrapper.",
                "Encode settings accept only inline arguments, for example 'zstd gzip'.",
                "Additional custom directives contain unbalanced braces.",
            ),
        )


if __name__ == "__main__":
    unittest.main()