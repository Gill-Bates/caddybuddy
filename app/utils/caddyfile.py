#!/usr/bin/env python3
#
# app/utils/caddyfile.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from io import StringIO

from app.utils.domains import split_domain_names

_CADDY_DIRECTIVE_KEYWORDS = frozenset(
    {
        "abort",
        "basic_auth",
        "basicauth",
        "bind",
        "encode",
        "error",
        "file_server",
        "handle",
        "handle_errors",
        "handle_path",
        "header",
        "import",
        "invoke",
        "log",
        "map",
        "method",
        "metrics",
        "php_fastcgi",
        "redir",
        "request_body",
        "respond",
        "reverse_proxy",
        "rewrite",
        "root",
        "route",
        "templates",
        "tls",
        "try_files",
        "uri",
        "vars",
    }
)
_SITE_HANDLER_DIRECTIVES = frozenset(
    {
        "file_server",
        "handle",
        "handle_path",
        "php_fastcgi",
        "redir",
        "respond",
        "reverse_proxy",
    }
)
_CADDY_TOKEN_FORBIDDEN_RE = re.compile(r"[\s{}#]", re.ASCII)
_SITE_LABEL_FORBIDDEN_RE = re.compile(r"[\r\n{}]", re.ASCII)
_DIRECTIVE_WRAPPER_TEMPLATE = r"^{directive}(?:\s+|\s*\{{)"
_DENIED_CUSTOM_DIRECTIVES = frozenset({"import"})
_NESTED_IMPORT_RE = re.compile(r"(?m)^\s*import(?:\s+|$)")
_SECURITY_HEADER_NAMES = frozenset(
    {
        "content-security-policy",
        "permissions-policy",
        "referrer-policy",
        "server",
        "strict-transport-security",
        "x-content-type-options",
        "x-frame-options",
        "x-permitted-cross-domain-policies",
        "x-powered-by",
    }
)


@dataclass(slots=True, frozen=True)
class DomainDirectiveBuildResult:
    upstream: str | None
    caddy_directives: str | None
    errors: tuple[str, ...] = ()


def normalize_caddy_directives(raw_value: str) -> str | None:
    """Normalize raw domain Caddy directives, accepting full site blocks or inner directives."""
    normalized = raw_value.strip()
    if not normalized:
        return None

    if "{" not in normalized:
        return normalized

    try:
        chunks = _split_top_level_directives(normalized)
    except ValueError:
        return normalized
    if len(chunks) != 1:
        return normalized

    header, body = _split_block_header_and_body(chunks[0])
    if body is None:
        return normalized

    first_token = header.split(maxsplit=1)[0].rstrip(",") if header else ""
    if first_token in _CADDY_DIRECTIVE_KEYWORDS:
        return normalized

    normalized = body.strip()

    return normalized or None


def _iter_text_lines(raw_value: str) -> Iterator[str]:
    for raw_line in StringIO(raw_value):
        yield raw_line.rstrip("\r\n")


def _normalize_caddy_token(value: str | None, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")  # noqa: TRY004 -- callers catch ValueError to collect validation errors

    normalized = value.strip()
    if not normalized:
        return ""
    if _CADDY_TOKEN_FORBIDDEN_RE.search(normalized):
        raise ValueError(f"{label} must be a single Caddy token")
    return normalized


def _normalize_site_label(value: str) -> str:
    normalized = value.strip() or "example.com"
    if _SITE_LABEL_FORBIDDEN_RE.search(normalized):
        raise ValueError("site label must not contain newlines or braces")
    return ", ".join(split_domain_names(normalized))


def _normalize_block_body(raw_value: str) -> str:
    return "\n".join(line.rstrip() for line in raw_value.strip().splitlines()).strip()


def _normalize_inline_directive_args(raw_value: str, directive_name: str) -> str:
    normalized = raw_value.strip()
    if normalized.startswith(f"{directive_name} "):
        normalized = normalized.removeprefix(f"{directive_name} ").strip()
    return " ".join(normalized.split())


def _is_comment_or_empty(value: str) -> bool:
    stripped = value.strip()
    return not stripped or stripped.startswith("#")


def _strip_caddy_comment(line: str) -> str:
    in_quote = False
    escaped = False
    result: list[str] = []
    for character in line:
        if escaped:
            result.append(character)
            escaped = False
            continue
        if character == "\\" and in_quote:
            result.append(character)
            escaped = True
            continue
        if character == '"':
            in_quote = not in_quote
            result.append(character)
            continue
        if character == "#" and not in_quote:
            break
        result.append(character)
    return "".join(result).rstrip()


def _strip_caddy_comments(raw_value: str) -> str:
    return "\n".join(_strip_caddy_comment(line) for line in raw_value.splitlines())


def _is_closing_brace_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("}") and _is_comment_or_empty(stripped[1:])


def _has_roughly_balanced_braces(raw_value: str) -> bool:
    depth = 0
    for character in raw_value:
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _contains_full_directive_wrapper(raw_value: str, directive_name: str) -> bool:
    normalized = raw_value.strip()
    return (
        re.match(
            _DIRECTIVE_WRAPPER_TEMPLATE.format(directive=re.escape(directive_name)),
            normalized,
        )
        is not None
    )


def _validate_block_body(label: str, directive_name: str, raw_value: str) -> str | None:
    normalized = raw_value.strip()
    if not normalized:
        return None
    if _contains_full_directive_wrapper(normalized, directive_name):
        return f"{label} expects only the inner directives, not the outer {directive_name} wrapper."
    if not _has_roughly_balanced_braces(normalized):
        return f"{label} contains unbalanced braces."
    return None


def _split_top_level_directives(directives: str) -> list[str]:
    chunks: list[str] = []
    current_lines: list[str] = []
    depth = 0

    for raw_line in directives.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            if depth == 0 and current_lines:
                chunks.append("\n".join(current_lines).strip())
                current_lines = []
            elif depth > 0:
                current_lines.append("")
            continue

        current_lines.append(line)
        depth += stripped.count("{") - stripped.count("}")
        if depth < 0:
            raise ValueError("Caddy directives contain an unmatched closing brace.")

        if depth == 0 and not stripped.endswith("{"):
            chunks.append("\n".join(current_lines).strip())
            current_lines = []

    if current_lines:
        chunks.append("\n".join(current_lines).strip())

    return [chunk for chunk in chunks if chunk]


def _validate_custom_directives(raw_value: str | None) -> list[str]:
    normalized = normalize_caddy_directives(raw_value or "")
    if not normalized:
        return []

    errors: list[str] = []

    # Check for import directive anywhere (including nested blocks)
    if _NESTED_IMPORT_RE.search(normalized):
        errors.append("Custom directive 'import' is not allowed.")
        return errors

    # Additional top-level directive validation
    for chunk in _split_top_level_directives(normalized):
        header, _body = _split_block_header_and_body(chunk)
        directive = header.split(maxsplit=1)[0].lower() if header else ""
        if directive in _DENIED_CUSTOM_DIRECTIVES:
            errors.append(f"Custom directive '{directive}' is not allowed.")

    return errors


def _split_block_header_and_body(chunk: str) -> tuple[str, str | None]:
    lines = [line.rstrip() for line in chunk.strip().splitlines()]
    if not lines:
        return "", None

    first_line = lines[0].strip()
    if len(lines) == 1:
        if "{" in first_line and "}" in first_line:
            header, _open, remainder = first_line.partition("{")
            body, _close, _tail = remainder.rpartition("}")
            if _is_comment_or_empty(_tail):
                return header.strip(), _normalize_block_body(body)
        return first_line, None

    if first_line.endswith("{") and _is_closing_brace_line(lines[-1]):
        return first_line[:-1].rstrip(), _normalize_block_body("\n".join(lines[1:-1]))

    return first_line, _normalize_block_body("\n".join(lines[1:]))


def build_domain_directives(
    *,
    upstream: str | None,
    reverse_proxy_options: str | None,
    encode_directives: str | None,
    header_directives: str | None,
    request_body_directives: str | None,
    log_directives: str | None,
    tls_directives: str | None,
    basic_auth_directives: str | None,
    custom_directives: str | None,
) -> str | None:
    """Build domain directives from structured managed blocks plus free-form custom directives."""
    normalized_upstream = _normalize_caddy_token(upstream, "Upstream")
    normalized_reverse_proxy_options = _normalize_block_body(reverse_proxy_options or "")
    normalized_encode_directives = _normalize_inline_directive_args(encode_directives or "", "encode")
    normalized_header_directives = _normalize_block_body(header_directives or "")
    normalized_request_body_directives = _normalize_block_body(request_body_directives or "")
    normalized_log_directives = _normalize_block_body(log_directives or "")
    normalized_tls_directives = _normalize_block_body(tls_directives or "")
    normalized_basic_auth_directives = _normalize_block_body(basic_auth_directives or "")
    normalized_custom_directives = normalize_caddy_directives(custom_directives or "")

    blocks: list[str] = []

    if normalized_basic_auth_directives:
        blocks.append(f"basic_auth {{\n{normalized_basic_auth_directives}\n}}")

    if normalized_upstream:
        if normalized_reverse_proxy_options:
            blocks.append(f"reverse_proxy {normalized_upstream} {{\n{normalized_reverse_proxy_options}\n}}")
        else:
            blocks.append(f"reverse_proxy {normalized_upstream}")

    if normalized_encode_directives:
        blocks.append(f"encode {normalized_encode_directives}")

    if normalized_header_directives:
        blocks.append(f"header {{\n{normalized_header_directives}\n}}")

    if normalized_request_body_directives:
        blocks.append(f"request_body {{\n{normalized_request_body_directives}\n}}")

    if normalized_log_directives:
        blocks.append(f"log {{\n{normalized_log_directives}\n}}")

    if normalized_tls_directives:
        blocks.append(f"tls {{\n{normalized_tls_directives}\n}}")

    if normalized_custom_directives:
        blocks.append(normalized_custom_directives)

    return "\n\n".join(blocks) or None


def prepare_domain_directives(
    *,
    upstream: str | None,
    reverse_proxy_options: str | None,
    encode_directives: str | None,
    header_directives: str | None,
    request_body_directives: str | None,
    log_directives: str | None,
    tls_directives: str | None,
    basic_auth_directives: str | None,
    custom_directives: str | None,
) -> DomainDirectiveBuildResult:
    """Normalize, validate, and build domain directives for previews and persistence."""
    errors: list[str] = []
    try:
        normalized_upstream = _normalize_caddy_token(upstream, "Upstream")
    except ValueError as exc:
        normalized_upstream = ""
        errors.append(str(exc))
    normalized_reverse_proxy_options = _normalize_block_body(reverse_proxy_options or "")
    normalized_encode_directives = _normalize_inline_directive_args(encode_directives or "", "encode")
    normalized_header_directives = _normalize_block_body(header_directives or "")
    normalized_request_body_directives = _normalize_block_body(request_body_directives or "")
    normalized_log_directives = _normalize_block_body(log_directives or "")
    normalized_tls_directives = _normalize_block_body(tls_directives or "")
    normalized_basic_auth_directives = _normalize_block_body(basic_auth_directives or "")
    normalized_custom_directives = normalize_caddy_directives(custom_directives or "")

    if not normalized_upstream and normalized_reverse_proxy_options:
        errors.append("Reverse proxy options require an upstream target.")

    for label, directive_name, raw_value in (
        ("Reverse proxy options", "reverse_proxy", normalized_reverse_proxy_options),
        ("Header block", "header", normalized_header_directives),
        ("Request body block", "request_body", normalized_request_body_directives),
        ("Log block", "log", normalized_log_directives),
        ("TLS block", "tls", normalized_tls_directives),
        ("Basic auth block", "basic_auth", normalized_basic_auth_directives),
    ):
        error = _validate_block_body(label, directive_name, raw_value)
        if error is not None:
            errors.append(error)

    if normalized_encode_directives and any(brace in normalized_encode_directives for brace in "{}"):
        errors.append("Encode settings accept only inline arguments, for example 'zstd gzip'.")

    if normalized_custom_directives and not _has_roughly_balanced_braces(normalized_custom_directives):
        errors.append("Additional custom directives appear to contain unbalanced braces.")

    try:
        errors.extend(_validate_custom_directives(custom_directives))
    except ValueError as exc:
        errors.append(str(exc))

    caddy_directives = build_domain_directives(
        upstream=normalized_upstream or None,
        reverse_proxy_options=normalized_reverse_proxy_options,
        encode_directives=normalized_encode_directives,
        header_directives=normalized_header_directives,
        request_body_directives=normalized_request_body_directives,
        log_directives=normalized_log_directives,
        tls_directives=normalized_tls_directives,
        basic_auth_directives=normalized_basic_auth_directives,
        custom_directives=normalized_custom_directives,
    )

    resolved_upstream = normalized_upstream or extract_upstream_from_directives(caddy_directives)
    return DomainDirectiveBuildResult(
        upstream=resolved_upstream,
        caddy_directives=caddy_directives,
        errors=tuple(errors),
    )


def extract_upstream_from_directives(directives: str | None) -> str | None:
    """Extract the first reverse_proxy target from stored directives, if present."""
    if not directives:
        return None

    try:
        chunks = _split_top_level_directives(directives)
    except ValueError:
        return None

    for chunk in chunks:
        header, _body = _split_block_header_and_body(chunk)
        if not header.startswith("reverse_proxy "):
            continue
        args = header.removeprefix("reverse_proxy ").split()
        return args[0] if len(args) == 1 else None

    return None


def extract_site_handler_from_directives(directives: str | None) -> str | None:
    """Extract the first top-level handler directive suitable for Sites UI badges."""
    if not directives:
        return None

    try:
        chunks = _split_top_level_directives(directives)
    except ValueError:
        return None

    for chunk in chunks:
        header, _body = _split_block_header_and_body(chunk)
        directive = header.split(None, 1)[0].strip()
        if directive in _SITE_HANDLER_DIRECTIVES:
            return directive

    return None


def build_domain_site_preview(
    *,
    name: str,
    upstream: str | None,
    caddy_directives: str | None,
    ssl_enabled: bool,
) -> str:
    """Build a human-readable Caddyfile site block preview for a domain."""
    normalized_name = _normalize_site_label(name)
    if ssl_enabled:
        site_label = normalized_name
    else:
        site_label = ", ".join(
            f"http://{domain_name}"
            for domain_name in split_domain_names(normalized_name)
        )

    normalized_directives = normalize_caddy_directives(caddy_directives or "")
    if normalized_directives is None:
        try:
            normalized_upstream = _normalize_caddy_token(upstream, "Upstream")
        except ValueError:
            normalized_upstream = ""

        if normalized_upstream:
            normalized_directives = f"reverse_proxy {normalized_upstream}"
        else:
            normalized_directives = "# Add Caddy directives here"

    indented = "\n".join(
        f"    {line}" if line.strip() else ""
        for line in _iter_text_lines(normalized_directives)
    )
    return f"{site_label} {{\n{indented}\n}}"


# ---------------------------------------------------------------------------
# Standard snippets and smart-import helpers
# ---------------------------------------------------------------------------

SNIPPET_SECURITY_HEADERS = """\
(security_headers) {
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
        -X-Powered-By
    }
}"""

_IMPORT_RE = re.compile(r"(?im)^\s*import\s+(\S+)")


def snippet_is_defined(text: str, snippet_name: str) -> bool:
    """Return True if (snippet_name) { ... } is defined anywhere in text."""
    return bool(
        re.search(
            r"(?m)^\s*\(\s*" + re.escape(snippet_name) + r"\s*\)\s*\{",
            text,
        )
    )


def directives_have_import(directives: str | None, import_name: str) -> bool:
    """Return True if directives contain `import import_name`."""
    if not directives:
        return False
    normalized_import_name = import_name.lower()
    for match in _IMPORT_RE.finditer(_strip_caddy_comments(directives)):
        if match.group(1).lower() == normalized_import_name:
            return True
    return False


def directives_have_log_block(directives: str | None) -> bool:
    """Return True if directives contain a top-level log block."""
    if not directives:
        return False
    try:
        chunks = _split_top_level_directives(_strip_caddy_comments(directives))
    except ValueError:
        return False
    for chunk in chunks:
        header, _body = _split_block_header_and_body(chunk)
        if header.split(maxsplit=1)[0].lower() == "log":
            return True
    return False


def directives_have_security_header_block(directives: str | None) -> bool:
    """Return True if directives contain a header block with at least one security header."""
    if not directives:
        return False
    try:
        chunks = _split_top_level_directives(_strip_caddy_comments(directives))
    except ValueError:
        return False
    for chunk in chunks:
        header, body = _split_block_header_and_body(chunk)
        if header.split(maxsplit=1)[0].lower() != "header":
            continue
        if body is None and header.startswith("header "):
            token = header.removeprefix("header ").strip().lstrip("+-?").split(maxsplit=1)[0].lower()
            if token in _SECURITY_HEADER_NAMES:
                return True
            continue
        if body is None:
            continue
        for raw_line in body.splitlines():
            token = raw_line.strip().lstrip("+-?").split(maxsplit=1)[0].lower()
            if token in _SECURITY_HEADER_NAMES:
                return True
    return False


def build_generated_site_block(
    *,
    name: str,
    upstream: str | None,
    caddy_directives: str | None,
    ssl_enabled: bool,
    import_security_headers: bool = False,
    import_default_log: bool = False,
) -> str:
    """Build a site block for the generated Caddyfile, optionally prepending standard imports."""
    preview = build_domain_site_preview(
        name=name,
        upstream=upstream,
        caddy_directives=caddy_directives,
        ssl_enabled=ssl_enabled,
    )
    if not import_security_headers and not import_default_log:
        return preview

    imports = []
    if import_security_headers:
        imports.append("    import security_headers")
    if import_default_log:
        imports.append("    import default_log")

    first_newline = preview.index("\n")
    header_line = preview[:first_newline]
    body = preview[first_newline:]
    return header_line + "\n" + "\n".join(imports) + "\n" + body


# Directives that configure certificates, listeners, or logging rather than request
# handling. A site in maintenance mode keeps them so TLS issuance keeps working.
_MAINTENANCE_PRESERVED_DIRECTIVES = frozenset({"bind", "log", "tls"})
# The page is embedded in a backtick-quoted Caddyfile token. Braces are encoded
# because Caddy expands {$ENV} while parsing and {placeholders} while responding;
# the numeric references decode identically in HTML text and attribute values.
_MAINTENANCE_RESPONSE_ESCAPES = str.maketrans({
    "`": "&#96;",
    "{": "&#123;",
    "}": "&#125;",
    "\r": " ",
    "\n": " ",
})
# Inline styles only: a <style> block would need braces. System colors follow the
# visitor's light/dark preference without a media query.
_MAINTENANCE_PAGE_TEMPLATE = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta name="color-scheme" content="light dark"><meta name="robots" content="noindex">'
    "<title>Service unavailable</title></head>"
    '<body style="margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;'
    "background:Canvas;color:CanvasText;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;"
    'line-height:1.6">'
    '<main style="box-sizing:border-box;max-width:42rem;margin:1rem;padding:2rem 2.5rem;'
    'border:1px solid color-mix(in srgb, CanvasText 15%, transparent);border-radius:12px">'
    "__BODY__</main></body></html>"
)


def build_maintenance_site_block(
    *,
    name: str,
    caddy_directives: str | None,
    body_html: str,
    import_security_headers: bool,
) -> str:
    """Build a site block that answers every request with the maintenance page (HTTP 503).

    Only certificate, listener, and logging directives of the site are kept; all
    request handlers are replaced. ``body_html`` must already be sanitized.
    """
    lines: list[str] = []
    normalized_directives = normalize_caddy_directives(caddy_directives or "")
    if normalized_directives:
        for chunk in _split_top_level_directives(normalized_directives):
            header, _body = _split_block_header_and_body(chunk)
            directive = header.split(maxsplit=1)[0].lower() if header else ""
            if directive in _MAINTENANCE_PRESERVED_DIRECTIVES:
                lines.append(chunk)

    response_body = _MAINTENANCE_PAGE_TEMPLATE.replace("__BODY__", body_html).translate(_MAINTENANCE_RESPONSE_ESCAPES)
    lines.extend((
        'header Content-Type "text/html; charset=utf-8"',
        'header Cache-Control "no-store"',
        f"respond `{response_body}` 503",
    ))
    return build_generated_site_block(
        name=name,
        upstream=None,
        caddy_directives="\n".join(lines),
        ssl_enabled=True,
        import_security_headers=import_security_headers,
    )


@dataclass(slots=True)
class ParsedCaddyfile:
    """Result of parsing a Caddyfile into its components."""
    global_block: str
    snippets: list[str]
    sites: list[tuple[str, str]]  # List of (domain, directives) tuples


_SNIPPET_HEADER_RE = re.compile(r"^\(([a-zA-Z0-9_-]+)\)\s*\{?\s*$")
_SITE_LABEL_PATTERN = re.compile(
    r"^(?:https?://)?(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}(?::\d+)?"
    r"(?:\s*,\s*(?:https?://)?(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}(?::\d+)?)*\s*$",
    re.ASCII | re.IGNORECASE,
)


def _is_global_block_header(header: str) -> bool:
    """Check if header is empty (global block) or just whitespace."""
    return not header.strip()


def _is_snippet_header(header: str) -> bool:
    """Check if header is a named snippet like (name)."""
    return _SNIPPET_HEADER_RE.match(header.strip()) is not None


def _is_site_label(header: str) -> bool:
    """Check if header looks like a domain or list of domains."""
    cleaned = header.strip().rstrip(",")
    if not cleaned:
        return False
    # Remove any matcher patterns like @name or /path
    if cleaned.startswith(("@", "/")):
        return False
    # Check if it's a Caddy directive keyword
    first_token = cleaned.split()[0].lower().rstrip(",")
    if first_token in _CADDY_DIRECTIVE_KEYWORDS:
        return False
    # Check for domain-like patterns
    return _SITE_LABEL_PATTERN.match(cleaned) is not None


def _extract_domain_from_label(label: str) -> str:
    """Extract clean domain names from a site label."""
    # Remove http:// or https:// prefixes and ports
    domains = []
    for part in label.split(","):
        part = part.strip()
        if part.startswith("http://"):
            part = part[7:]
        elif part.startswith("https://"):
            part = part[8:]
        # Remove port if present
        if ":" in part:
            part = part.split(":")[0]
        if part:
            domains.append(part.lower())
    return ", ".join(domains)


def parse_caddyfile(content: str) -> ParsedCaddyfile:
    """Parse a Caddyfile into global block, snippets, and site blocks.

    Returns a ParsedCaddyfile with:
    - global_block: The global options block and any top-level directives
    - snippets: List of named snippet blocks like (name) { ... }
    - sites: List of (domain, directives) tuples for site blocks
    """
    lines = content.splitlines()
    global_parts: list[str] = []
    snippets: list[str] = []
    sites: list[tuple[str, str]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            global_parts.append(line)
            i += 1
            continue

        if "{" in stripped:
            header_end = stripped.find("{")
            header = stripped[:header_end].strip()
            inline_remainder = stripped[header_end + 1:].strip()

            block_lines = [line]
            depth = stripped.count("{") - stripped.count("}")

            if depth == 0 and "}" in stripped:
                body = inline_remainder.rstrip("}").strip()
            else:
                i += 1
                while i < len(lines) and depth > 0:
                    block_line = lines[i]
                    block_lines.append(block_line)
                    depth += block_line.count("{") - block_line.count("}")
                    i += 1
                i -= 1  # Compensate for the loop's unconditional increment below.

                body_lines = block_lines[1:-1] if len(block_lines) > 2 else []
                body = "\n".join(body_lines).strip()

            if _is_global_block_header(header):
                global_parts.append("\n".join(block_lines))
            elif _is_snippet_header(header):
                # Snippets are part of the baseline, so they also go into global_parts.
                global_parts.append("\n".join(block_lines))
                snippets.append("\n".join(block_lines))
            elif _is_site_label(header):
                domain = _extract_domain_from_label(header)
                sites.append((domain, body))
            else:
                global_parts.append("\n".join(block_lines))

            i += 1
        else:
            global_parts.append(line)
            i += 1

    global_block = "\n".join(global_parts).strip()

    return ParsedCaddyfile(
        global_block=global_block,
        snippets=snippets,
        sites=sites,
    )


# The Caddy admin endpoint and ACME email are owned by CaddyBuddy settings, not the
# stored baseline, so any baseline-level admin/email lines are stripped and replaced.
_MANAGED_GLOBAL_DIRECTIVE_RE = re.compile(r"^\s*(?:admin|email)\b")


def inject_global_options(baseline: str, *, admin: str | None, email: str | None) -> str:
    """Return ``baseline`` with CaddyBuddy-managed admin/email directives in the global block.

    ``admin`` and ``email`` are sourced from Settings (Caddy Admin URL and the SSL Labs
    contact email). Existing ``admin``/``email`` lines in the global options block are
    removed first so Settings remains the single source of truth. A global options block
    is created when the baseline does not already define one.
    """
    managed: list[str] = []
    if admin:
        managed.append(f"    admin {admin}")
    if email:
        managed.append(f"    email {email}")

    if not managed:
        return baseline.strip()

    lines = baseline.splitlines()

    # The global options block opens with a bare top-level "{" (snippets are "(name) {"
    # and sites are "domain {"), so locate the first such line at brace depth zero.
    depth = 0
    open_idx: int | None = None
    for idx, line in enumerate(lines):
        if depth == 0 and line.strip() == "{":
            open_idx = idx
            break
        depth += line.count("{") - line.count("}")
        depth = max(depth, 0)

    if open_idx is None:
        block = "\n".join(["{", *managed, "}"])
        rest = baseline.strip()
        return f"{block}\n\n{rest}".strip() if rest else block

    # Find the matching closing brace for the global block.
    depth = 0
    close_idx = len(lines) - 1
    for idx in range(open_idx, len(lines)):
        depth += lines[idx].count("{") - lines[idx].count("}")
        if depth == 0:
            close_idx = idx
            break

    body = [
        line
        for line in lines[open_idx + 1:close_idx]
        if not _MANAGED_GLOBAL_DIRECTIVE_RE.match(line)
    ]

    new_lines = lines[: open_idx + 1] + managed + body + lines[close_idx:]
    return "\n".join(new_lines).strip()
