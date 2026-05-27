#!/usr/bin/env python3
#
# tests/test_caddyfile_parser.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Tests for Caddyfile parsing utilities."""

import pytest

from app.utils.caddyfile import ParsedCaddyfile, parse_caddyfile


class TestParseCaddyfile:
    """Tests for parse_caddyfile function."""

    def test_parse_empty_caddyfile(self) -> None:
        result = parse_caddyfile("")
        assert result.global_block == ""
        assert result.snippets == []
        assert result.sites == []

    def test_parse_global_block_only(self) -> None:
        caddyfile = """{
    admin 127.0.0.1:2019
    auto_https off
}"""
        result = parse_caddyfile(caddyfile)
        assert "admin 127.0.0.1:2019" in result.global_block
        assert result.snippets == []
        assert result.sites == []

    def test_parse_snippet_only(self) -> None:
        caddyfile = """(security_headers) {
    header {
        X-Frame-Options DENY
    }
}"""
        result = parse_caddyfile(caddyfile)
        assert "(security_headers)" in result.global_block
        assert len(result.snippets) == 1
        assert result.sites == []

    def test_parse_site_only(self) -> None:
        caddyfile = """example.com {
    reverse_proxy localhost:8080
}"""
        result = parse_caddyfile(caddyfile)
        assert "example.com" not in result.global_block
        assert result.snippets == []
        assert len(result.sites) == 1
        domain, directives = result.sites[0]
        assert domain == "example.com"
        assert "reverse_proxy localhost:8080" in directives

    def test_parse_full_caddyfile(self) -> None:
        caddyfile = """{
    admin 127.0.0.1:2019
}

(security_headers) {
    header {
        X-Frame-Options DENY
    }
}

caddy.example.com {
    reverse_proxy localhost:8080
    import security_headers
}

another.example.com {
    file_server
    root * /var/www/html
}"""
        result = parse_caddyfile(caddyfile)

        # Global block contains admin settings and snippets
        assert "admin 127.0.0.1:2019" in result.global_block
        assert "(security_headers)" in result.global_block

        # Snippets are extracted
        assert len(result.snippets) == 1
        assert "(security_headers)" in result.snippets[0]

        # Sites are extracted
        assert len(result.sites) == 2
        domains = [s[0] for s in result.sites]
        assert "caddy.example.com" in domains
        assert "another.example.com" in domains

        # Site blocks not in global
        assert "caddy.example.com" not in result.global_block
        assert "another.example.com" not in result.global_block

    def test_parse_site_with_http_prefix(self) -> None:
        caddyfile = """http://example.com {
    respond "Hello"
}"""
        result = parse_caddyfile(caddyfile)
        assert len(result.sites) == 1
        domain, _ = result.sites[0]
        assert domain == "example.com"

    def test_parse_site_with_https_prefix(self) -> None:
        caddyfile = """https://secure.example.com {
    respond "Secure"
}"""
        result = parse_caddyfile(caddyfile)
        assert len(result.sites) == 1
        domain, _ = result.sites[0]
        assert domain == "secure.example.com"

    def test_parse_site_with_port(self) -> None:
        caddyfile = """example.com:443 {
    respond "Port"
}"""
        result = parse_caddyfile(caddyfile)
        assert len(result.sites) == 1
        domain, _ = result.sites[0]
        assert domain == "example.com"

    def test_parse_multi_domain_site(self) -> None:
        caddyfile = """example.com, www.example.com {
    respond "Multi"
}"""
        result = parse_caddyfile(caddyfile)
        assert len(result.sites) == 1
        domain, _ = result.sites[0]
        assert "example.com" in domain
        assert "www.example.com" in domain

    def test_parse_preserves_comments(self) -> None:
        caddyfile = """# Global comment
{
    admin off
}

# Site comment
example.com {
    respond "Hello"
}"""
        result = parse_caddyfile(caddyfile)
        assert "# Global comment" in result.global_block
        assert len(result.sites) == 1

    def test_parse_multiple_snippets(self) -> None:
        caddyfile = """(snippet1) {
    header X-1 "value1"
}

(snippet2) {
    header X-2 "value2"
}"""
        result = parse_caddyfile(caddyfile)
        assert len(result.snippets) == 2
        assert result.sites == []

    def test_parse_returns_dataclass(self) -> None:
        result = parse_caddyfile("example.com { respond ok }")
        assert isinstance(result, ParsedCaddyfile)
        assert hasattr(result, "global_block")
        assert hasattr(result, "snippets")
        assert hasattr(result, "sites")


class TestValidateCustomDirectives:
    """Tests for _validate_custom_directives function."""

    def test_rejects_top_level_import(self) -> None:
        from app.utils.caddyfile import _validate_custom_directives

        errors = _validate_custom_directives("import /etc/caddy/*")
        assert len(errors) == 1
        assert "import" in errors[0].lower()

    def test_rejects_nested_import(self) -> None:
        from app.utils.caddyfile import _validate_custom_directives

        errors = _validate_custom_directives("""
route {
    import /etc/caddy/*
}
""")
        assert len(errors) == 1
        assert "import" in errors[0].lower()

    def test_allows_clean_directives(self) -> None:
        from app.utils.caddyfile import _validate_custom_directives

        errors = _validate_custom_directives("""
reverse_proxy localhost:8080
encode gzip
""")
        assert errors == []

    def test_allows_empty_directives(self) -> None:
        from app.utils.caddyfile import _validate_custom_directives

        errors = _validate_custom_directives("")
        assert errors == []
        errors = _validate_custom_directives(None)
        assert errors == []
