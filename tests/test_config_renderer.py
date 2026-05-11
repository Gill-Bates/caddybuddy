#!/usr/bin/env python3
#
# tests/test_config_renderer.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest

from app.models.entities import ConfigTemplate, Site
from app.services.config_renderer import config_renderer


class ConfigRendererTests(unittest.TestCase):
    def test_render_template_ignores_invalid_unused_variable_values(self) -> None:
        result = config_renderer.render_template(
            "reverse_proxy {{upstream}}",
            {
                "upstream": "127.0.0.1:8080",
                "unused": "bad\nvalue",
            },
        )

        self.assertEqual(result.missing_vars, ())
        self.assertEqual(result.warnings, ())
        self.assertEqual(result.rendered, "reverse_proxy 127.0.0.1:8080")

    def test_validate_template_variables_treats_upstream_as_site_provided(self) -> None:
        template = ConfigTemplate(
            name="reverse-proxy",
            description=None,
            caddyfile="reverse_proxy {{upstream}}\nheader X-Site {{domain}}",
            checksum="x" * 64,
            variables={},
        )

        defined_vars, undefined_vars = config_renderer.validate_template_variables(template)

        self.assertEqual(defined_vars, set())
        self.assertEqual(undefined_vars, set())

    def test_render_site_config_uses_upstream_from_site_variables(self) -> None:
        template = ConfigTemplate(
            name="reverse-proxy",
            description=None,
            caddyfile="reverse_proxy {{upstream}}",
            checksum="y" * 64,
            variables={},
        )
        site = Site(
            domain="example.com",
            config_template_id=1,
            enabled=True,
            description=None,
            variables={"upstream": "127.0.0.1:8080"},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
        )

        result = config_renderer.render_site_config(site, template)

        self.assertEqual(result.missing_vars, ())
        self.assertIn("reverse_proxy 127.0.0.1:8080", result.rendered)
        self.assertNotIn("{{upstream}}", result.rendered)

    def test_render_site_config_omits_none_ssl_enabled_reserved_var(self) -> None:
        template = ConfigTemplate(
            name="tls-optional",
            description=None,
            caddyfile="header X-TLS {{ssl_enabled}}\nrespond ok",
            checksum="z" * 64,
            variables={},
        )
        site = Site(
            domain="example.com",
            config_template_id=1,
            enabled=True,
            description=None,
            variables={},
            ssl_enabled=None,
            ssl_provider="letsencrypt",
        )

        result = config_renderer.render_site_config(site, template)

        self.assertEqual(result.missing_vars, ("ssl_enabled",))
        self.assertIn("{{ssl_enabled}}", result.rendered)

    def test_render_server_caddyfile_preserves_duplicate_missing_variables(self) -> None:
        template = ConfigTemplate(
            name="needs-upstream",
            description=None,
            caddyfile="reverse_proxy {{upstream}}",
            checksum="a" * 64,
            variables={},
        )
        first_site = Site(
            domain="one.example.com",
            config_template_id=1,
            enabled=True,
            description=None,
            variables={},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
        )
        second_site = Site(
            domain="two.example.com",
            config_template_id=1,
            enabled=True,
            description=None,
            variables={},
            ssl_enabled=True,
            ssl_provider="letsencrypt",
        )

        result = config_renderer.render_server_caddyfile([
            (first_site, template),
            (second_site, template),
        ])

        self.assertEqual(result.missing_vars, ("upstream", "upstream"))


if __name__ == "__main__":
    unittest.main()