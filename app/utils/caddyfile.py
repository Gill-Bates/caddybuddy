#!/usr/bin/env python3
#
# app/utils/caddyfile.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import re
from io import StringIO
from dataclasses import dataclass


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
_REVERSE_PROXY_TARGET_RE = re.compile(r"^\s*reverse_proxy\s+([^\s{#]+)", re.MULTILINE)

CADDY_DIRECTIVES_EXAMPLE = """reverse_proxy 10.30.0.140:8000 {
    transport http {
        keepalive 30s
    }

    header_up Host {host}
    header_up Authorization {http.request.header.Authorization}
}

encode gzip

header {
    Strict-Transport-Security \"max-age=31536000; includeSubDomains; preload\"
    X-Content-Type-Options \"nosniff\"
    X-Frame-Options \"DENY\"
    Referrer-Policy \"strict-origin-when-cross-origin\"
    -Server
    -X-Powered-By
}

request_body {
    max_size 100MB
}

log {
    output file /var/log/caddy/access.log {
        roll_size 10mb
    }
}"""


@dataclass(slots=True)
class DomainDirectiveFormState:
    upstream: str | None = None
    reverse_proxy_options: str = ""
    encode_directives: str = ""
    header_directives: str = ""
    request_body_directives: str = ""
    log_directives: str = ""
    tls_directives: str = ""
    basic_auth_directives: str = ""
    custom_directives: str = ""


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

    chunks = _split_top_level_directives(normalized)
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


def _iter_text_lines(raw_value: str):
    for raw_line in StringIO(raw_value):
        yield raw_line.rstrip("\r\n")


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


def _is_closing_brace_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("}") and _is_comment_or_empty(stripped[1:])


def _has_balanced_braces(raw_value: str) -> bool:
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
    return normalized.startswith(f"{directive_name} ") or normalized.startswith(f"{directive_name}{{")


def _validate_block_body(label: str, directive_name: str, raw_value: str) -> str | None:
    normalized = raw_value.strip()
    if not normalized:
        return None
    if _contains_full_directive_wrapper(normalized, directive_name):
        return f"{label} expects only the inner directives, not the outer {directive_name} wrapper."
    if not _has_balanced_braces(normalized):
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
        depth = max(depth, 0)

        if depth == 0 and not stripped.endswith("{"):
            chunks.append("\n".join(current_lines).strip())
            current_lines = []

    if current_lines:
        chunks.append("\n".join(current_lines).strip())

    return [chunk for chunk in chunks if chunk]


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


def parse_domain_directive_form_state(
    directives: str | None,
    *,
    upstream_fallback: str | None = None,
) -> DomainDirectiveFormState:
    """Split stored directives into managed blocks and additional custom directives."""
    normalized_directives = normalize_caddy_directives(directives or "")
    if normalized_directives is None:
        return DomainDirectiveFormState(upstream=upstream_fallback)

    state = DomainDirectiveFormState(upstream=upstream_fallback)
    custom_chunks: list[str] = []

    for chunk in _split_top_level_directives(normalized_directives):
        header, body = _split_block_header_and_body(chunk)
        if header.startswith("reverse_proxy "):
            target = header.removeprefix("reverse_proxy ").strip() or None
            if state.upstream is None and target is not None:
                state.upstream = target
                state.reverse_proxy_options = body or ""
                continue
            custom_chunks.append(chunk)
            continue

        if header.startswith("encode ") and body is None:
            if not state.encode_directives:
                state.encode_directives = header.removeprefix("encode ").strip()
            else:
                custom_chunks.append(chunk)
            continue

        if header == "header":
            if not state.header_directives:
                state.header_directives = body or ""
            else:
                custom_chunks.append(chunk)
            continue

        if header == "request_body":
            if not state.request_body_directives:
                state.request_body_directives = body or ""
            else:
                custom_chunks.append(chunk)
            continue

        if header == "log":
            if not state.log_directives:
                state.log_directives = body or ""
            else:
                custom_chunks.append(chunk)
            continue

        if header == "tls":
            if not state.tls_directives:
                state.tls_directives = body or ""
            else:
                custom_chunks.append(chunk)
            continue

        if header in {"basic_auth", "basicauth"}:
            if not state.basic_auth_directives:
                state.basic_auth_directives = body or ""
            else:
                custom_chunks.append(chunk)
            continue

        custom_chunks.append(chunk)

    state.custom_directives = "\n\n".join(custom_chunks)
    return state


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
    normalized_upstream = upstream.strip() if isinstance(upstream, str) else ""
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
    normalized_upstream = upstream.strip() if isinstance(upstream, str) else ""
    normalized_reverse_proxy_options = _normalize_block_body(reverse_proxy_options or "")
    normalized_encode_directives = _normalize_inline_directive_args(encode_directives or "", "encode")
    normalized_header_directives = _normalize_block_body(header_directives or "")
    normalized_request_body_directives = _normalize_block_body(request_body_directives or "")
    normalized_log_directives = _normalize_block_body(log_directives or "")
    normalized_tls_directives = _normalize_block_body(tls_directives or "")
    normalized_basic_auth_directives = _normalize_block_body(basic_auth_directives or "")
    normalized_custom_directives = normalize_caddy_directives(custom_directives or "")

    errors: list[str] = []
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

    if normalized_custom_directives and not _has_balanced_braces(normalized_custom_directives):
        errors.append("Additional custom directives contain unbalanced braces.")

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

    match = _REVERSE_PROXY_TARGET_RE.search(directives)
    return match.group(1) if match else None


def build_domain_site_preview(
    *,
    name: str,
    upstream: str | None,
    caddy_directives: str | None,
    ssl_enabled: bool,
) -> str:
    """Build a human-readable Caddyfile site block preview for a domain."""
    normalized_name = name.strip() or "example.com"
    site_label = normalized_name if ssl_enabled else f"http://{normalized_name}"

    normalized_directives = normalize_caddy_directives(caddy_directives or "")
    if normalized_directives is None:
        if upstream:
            normalized_directives = f"reverse_proxy {upstream}"
        else:
            normalized_directives = "# Add Caddy directives here"

    indented = "\n".join(
        f"    {line}" if line.strip() else ""
        for line in _iter_text_lines(normalized_directives)
    )
    return f"{site_label} {{\n{indented}\n}}"