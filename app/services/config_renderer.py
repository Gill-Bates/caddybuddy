#!/usr/bin/env python3
#
# app/services/config_renderer.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Configuration rendering service.

Handles template variable substitution and Caddyfile generation.
This is the render pipeline: Template + Variables → Rendered Config

Flow:
    ConfigTemplate.caddyfile
    + Site.variables (overrides template defaults)
    + ConfigTemplate.variables (defaults)
    → Rendered Caddyfile ready for validation/deployment
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from app.models.entities import ConfigTemplate, Site
from app.utils.caddyfile import build_domain_site_preview


# Variable pattern: {{variable_name}} or {{ variable_name }}
_VAR_PATTERN: Final[re.Pattern[str]] = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_INVALID_VAR_CHARS: Final[re.Pattern[str]] = re.compile(r"[\{\}\n\r]")

# Reserved variable names that are auto-populated
_RESERVED_VARS: Final[frozenset[str]] = frozenset({
    "domain",
    "ssl_enabled",
    "ssl_provider",
})
_SITE_PROVIDED_VARS: Final[frozenset[str]] = frozenset({"upstream"})


@dataclass(slots=True, frozen=True)
class RenderResult:
    """Result of configuration rendering."""

    rendered: str
    missing_vars: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def has_errors(self) -> bool:
        return len(self.missing_vars) > 0


class ConfigRenderError(Exception):
    """Raised when configuration rendering fails."""

    def __init__(self, message: str, missing_vars: tuple[str, ...] = ()) -> None:
        self.missing_vars = missing_vars
        super().__init__(message)


class ConfigRenderer:
    """Service for rendering configuration templates.

    Handles:
    - Variable substitution ({{var_name}} syntax)
    - Reserved variable population (domain, ssl_enabled, etc.)
    - Missing variable detection
    - Caddyfile site block generation
    """

    @staticmethod
    def extract_variables(template: str) -> set[str]:
        """Extract all variable names from a template."""
        return set(_VAR_PATTERN.findall(template))

    @staticmethod
    def _validate_variable_value(name: str, value: str) -> str:
        if _INVALID_VAR_CHARS.search(value):
            raise ConfigRenderError(
                f"Variable '{name}' contains characters illegal in Caddyfile substitution"
            )
        return value

    @staticmethod
    def merge_variables(
        template_vars: dict[str, object],
        site_vars: dict[str, object],
        reserved_vars: dict[str, object],
    ) -> dict[str, str]:
        """Merge variable sources with correct precedence.

        Precedence (highest to lowest):
        1. Reserved variables (domain, ssl_enabled, etc.)
        2. Site-specific variables
        3. Template default variables
        """
        merged = dict(template_vars)
        merged.update(site_vars)
        merged.update(reserved_vars)
        return {k: str(v) for k, v in merged.items() if v is not None}

    def render_template(
        self,
        template: str,
        variables: dict[str, str],
        *,
        strict: bool = False,
    ) -> RenderResult:
        """Render a template with variable substitution.

        Args:
            template: Caddyfile template with {{var}} placeholders
            variables: Variable values to substitute
            strict: If True, raise on missing variables

        Returns:
            RenderResult with rendered config and any warnings
        """
        required_vars = self.extract_variables(template)
        provided_vars = set(variables.keys())
        missing_vars = required_vars - provided_vars
        warnings: list[str] = []

        if missing_vars and strict:
            raise ConfigRenderError(
                f"Missing required variables: {', '.join(sorted(missing_vars))}",
                missing_vars=tuple(sorted(missing_vars)),
            )

        sanitized_variables = {
            name: self._validate_variable_value(name, variables[name])
            for name in required_vars & provided_vars
        }

        def replace_var(match: re.Match[str]) -> str:
            var_name = match.group(1)
            if var_name in sanitized_variables:
                return sanitized_variables[var_name]
            warnings.append(f"Variable '{var_name}' not defined, left as placeholder")
            return match.group(0)

        rendered = _VAR_PATTERN.sub(replace_var, template)

        return RenderResult(
            rendered=rendered,
            missing_vars=tuple(sorted(missing_vars)),
            warnings=tuple(warnings),
        )

    def render_site_config(
        self,
        site: Site,
        template: ConfigTemplate,
        *,
        strict: bool = False,
    ) -> RenderResult:
        """Render a complete site configuration.

        Combines template with site-specific variables and generates
        a complete Caddyfile site block.

        Args:
            site: Site entity with domain and variables
            template: ConfigTemplate with caddyfile and default variables
            strict: If True, raise on missing variables

        Returns:
            RenderResult with rendered Caddyfile site block
        """
        reserved_vars = {
            "domain": site.domain,
            "ssl_enabled": str(site.ssl_enabled).lower() if site.ssl_enabled is not None else None,
            "ssl_provider": site.ssl_provider,
        }

        merged_vars = self.merge_variables(
            template.variables or {},
            site.variables or {},
            reserved_vars,
        )

        # Render the inner directives from template
        inner_result = self.render_template(
            template.caddyfile,
            merged_vars,
            strict=strict,
        )

        # Build complete site block
        site_block = build_domain_site_preview(
            name=self._validate_variable_value("domain", site.domain),
            upstream=None,  # Upstream is part of template directives
            caddy_directives=inner_result.rendered,
            ssl_enabled=site.ssl_enabled,
        )

        return RenderResult(
            rendered=site_block,
            missing_vars=inner_result.missing_vars,
            warnings=inner_result.warnings,
        )

    def render_server_caddyfile(
        self,
        sites: list[tuple[Site, ConfigTemplate]],
        *,
        strict: bool = False,
    ) -> RenderResult:
        """Render a complete Caddyfile for multiple sites on a server.

        Args:
            sites: List of (Site, ConfigTemplate) tuples to render
            strict: If True, raise on missing variables

        Returns:
            RenderResult with complete multi-site Caddyfile
        """
        site_blocks: list[str] = []
        all_missing_vars: list[str] = []
        all_warnings: list[str] = []

        for site, template in sites:
            result = self.render_site_config(site, template, strict=strict)
            site_blocks.append(result.rendered)
            all_missing_vars.extend(result.missing_vars)
            all_warnings.extend(result.warnings)

        rendered = "\n\n".join(site_blocks)

        return RenderResult(
            rendered=rendered,
            missing_vars=tuple(sorted(all_missing_vars)),
            warnings=tuple(all_warnings),
        )

    def validate_template_variables(
        self,
        template: ConfigTemplate,
    ) -> tuple[set[str], set[str]]:
        """Validate template variables against defined defaults.

        Returns:
            Tuple of (defined_vars, undefined_vars)
        """
        required_vars = self.extract_variables(template.caddyfile)
        non_reserved = required_vars - _RESERVED_VARS - _SITE_PROVIDED_VARS
        defined_vars = set(template.variables.keys()) if template.variables else set()
        undefined_vars = non_reserved - defined_vars

        return defined_vars, undefined_vars


config_renderer = ConfigRenderer()
