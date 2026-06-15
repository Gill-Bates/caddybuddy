#!/usr/bin/env python3
#
# app/services/caddy.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import json
import logging
import socket
import shutil
from collections.abc import Iterable
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config.settings import get_settings
from app.utils.admin_targets import (
    ResolvedIPAddress,
    is_allowed_admin_ip,
    validate_admin_host,
)

_DNS_RESOLUTION_TIMEOUT = 5.0
_MAX_CADDYFILE_BYTES = 512 * 1024
_MAX_ADMIN_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_ADMIN_ERROR_BODY_CHARS = 240
_MAX_CERT_PURGE_SCAN_PATHS = 20_000


logger = logging.getLogger(__name__)


class CaddyServiceError(Exception):
    """Domain exception for Caddy Admin API transport failures."""


def _authority_header(host: str, port: int | None) -> str:
    authority_host = host
    if ":" in authority_host:
        authority_host = f"[{authority_host}]"
    return f"{authority_host}:{port}" if port is not None else authority_host


async def _resolve_target_ips(host: str, port: int | None) -> list[ResolvedIPAddress]:
    try:
        return [ip_address(host)]
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            address_info = await asyncio.wait_for(
                loop.getaddrinfo(
                    host,
                    port,
                    family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                ),
                timeout=_DNS_RESOLUTION_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise ValueError(f"DNS resolution timed out for Caddy admin host: {host!r}") from exc
        except OSError as exc:
            raise ValueError(f"Failed to resolve Caddy admin host: {host!r}") from exc

    resolved_ips: list[ResolvedIPAddress] = []
    seen: set[ResolvedIPAddress] = set()
    for *_prefix, sockaddr in address_info:
        if not sockaddr or not sockaddr[0]:
            continue
        target_ip = ip_address(sockaddr[0])
        if target_ip in seen:
            continue
        seen.add(target_ip)
        resolved_ips.append(target_ip)

    if not resolved_ips:
        raise ValueError(f"Failed to resolve Caddy admin host: {host!r}")
    return resolved_ips


def _validate_caddyfile_size(caddyfile: str) -> None:
    if len(caddyfile.encode("utf-8")) > _MAX_CADDYFILE_BYTES:
        raise CaddyServiceError(f"Caddyfile exceeds the {_MAX_CADDYFILE_BYTES} byte limit.")


def _summarize_http_error_response(response: httpx.Response) -> str | None:
    if len(response.content) > _MAX_ADMIN_RESPONSE_BYTES:
        return "response body too large"

    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        payload = None

    if isinstance(payload, (dict, list)):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    else:
        try:
            body = " ".join(response.text.split())
        except Exception:  # pragma: no cover - defensive fallback for broken encodings
            return None

    if not body:
        return None

    if len(body) > _MAX_ADMIN_ERROR_BODY_CHARS:
        body = body[:_MAX_ADMIN_ERROR_BODY_CHARS].rstrip() + "..."

    return body


def _is_format_warning(warning: Any) -> bool:
    if isinstance(warning, dict):
        msg = warning.get("message", "")
    else:
        msg = str(warning)
    return "not formatted" in msg.lower()


def _normalize_adapted_config(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise CaddyServiceError(
            "Caddy Admin API /adapt response must include a JSON object result."
        )
    warnings_raw = payload.get("warnings", [])
    if not isinstance(warnings_raw, list):
        raise CaddyServiceError("Caddy Admin API /adapt warnings must be a list.")
    non_format_warnings = [w for w in warnings_raw if not _is_format_warning(w)]
    if non_format_warnings:
        logger.warning("Caddy adapter returned %d non-format warnings.", len(non_format_warnings))
        logger.debug("Caddy adapter warnings: %s", non_format_warnings)
    return result, warnings_raw


class CaddyAdminClient:
    _LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._base_url = self._normalize_base_url(base_url)
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"Unsupported Caddy admin scheme: {parsed.scheme!r}")
        if parsed.username or parsed.password:
            raise ValueError("Caddy admin URL must not include username or password.")
        if not parsed.hostname:
            raise ValueError("Caddy admin URL must include a host.")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Caddy admin URL must not include a path, query, or fragment.")

        try:
            parsed_port = parsed.port
        except ValueError as exc:
            raise ValueError("Caddy admin URL has an invalid port.") from exc

        port = parsed_port or (443 if parsed.scheme == "https" else 80)
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        return urlunsplit((parsed.scheme, f"{host}:{port}", "", "", ""))

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, limits=self._LIMITS)
        return self._client

    async def _bounded_request(
        self,
        method: str,
        endpoint: str,
        *,
        headers: dict[str, str],
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a request and stream the response body up to _MAX_ADMIN_RESPONSE_BYTES.

        Raises CaddyServiceError immediately if the response body exceeds the limit,
        preventing large allocations before the per-caller size checks trigger.
        """
        client = self._get_client()
        req = client.build_request(method, endpoint, headers=headers, **kwargs)
        resp = await client.send(req, stream=True)
        try:
            body = bytearray()
            truncated = False
            async for chunk in resp.aiter_bytes():
                if len(body) + len(chunk) > _MAX_ADMIN_RESPONSE_BYTES:
                    truncated = True
                    break
                body.extend(chunk)
        finally:
            await resp.aclose()

        if truncated:
            status = resp.status_code
            if status >= 400:
                raise CaddyServiceError(
                    f"Caddy Admin API request failed with status {status}."
                    " Response body: response body too large"
                )
            raise CaddyServiceError("Caddy Admin API response is too large.")

        bounded = httpx.Response(
            status_code=resp.status_code,
            headers=resp.headers,
            content=bytes(body),
            request=req,
        )
        bounded.raise_for_status()
        return bounded

    async def aclose(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    async def __aenter__(self) -> CaddyAdminClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    @staticmethod
    def _merge_headers(extra_headers: dict[str, str] | None, host: str, port: int | None) -> dict[str, str]:
        # The pinned Host header is part of the IP-pinning contract and must not
        # be overridable by callers, so apply caller headers first and force Host last.
        merged = dict(extra_headers or {})
        merged.pop("Host", None)
        merged.pop("host", None)
        merged["Host"] = _authority_header(host, port)
        return merged

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        parsed = urlsplit(self._base_url)
        host = (parsed.hostname or "").lower()
        extra_headers = kwargs.pop("headers", None)

        try:
            validate_admin_host(host)
            resolved_ips = await _resolve_target_ips(host, parsed.port)
            validated_ip: ResolvedIPAddress | None = None
            for target_ip in resolved_ips:
                if not is_allowed_admin_ip(target_ip):
                    raise CaddyServiceError(
                        "Only loopback or private Caddy admin targets are allowed."
                    )
                if validated_ip is None:
                    validated_ip = target_ip

            if validated_ip is None:
                raise CaddyServiceError(f"Failed to resolve Caddy admin host: {host!r}")

            pinned_host = str(validated_ip)
            if ":" in pinned_host:
                pinned_host = f"[{pinned_host}]"

            endpoint = urlunsplit((parsed.scheme, f"{pinned_host}:{parsed.port}", path, "", ""))
            return await self._bounded_request(
                method,
                endpoint,
                headers=self._merge_headers(extra_headers, parsed.hostname or host, parsed.port),
                **kwargs,
            )
        except CaddyServiceError:
            raise
        except httpx.TimeoutException as exc:
            raise CaddyServiceError("Caddy Admin API request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            details = _summarize_http_error_response(exc.response)
            message = f"Caddy Admin API request failed with status {exc.response.status_code}."
            if details:
                message = f"{message} Response body: {details}"
            raise CaddyServiceError(
                message
            ) from exc
        except httpx.HTTPError as exc:
            raise CaddyServiceError("Caddy Admin API unavailable.") from exc
        except ValueError as exc:
            raise CaddyServiceError(str(exc)) from exc

    async def health(self) -> bool:
        try:
            await self._request("GET", "/config/")
        except CaddyServiceError:
            return False
        return True

    async def load_config(self, config: dict[str, Any]) -> None:
        await self.load_config_force(config, force_reload=False)

    async def load_config_force(self, config: dict[str, Any], *, force_reload: bool) -> None:
        if not isinstance(config, dict):
            raise TypeError("config must be a JSON object.")
        headers = {"Cache-Control": "must-revalidate"} if force_reload else None
        await self._request("POST", "/load", json=config, headers=headers)

    async def adapt_caddyfile(self, caddyfile: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return (adapted_config, warnings) from the Caddy /adapt endpoint."""
        response = await self._request(
            "POST",
            "/adapt",
            content=caddyfile.encode("utf-8"),
            headers={"Content-Type": "text/caddyfile"},
        )
        if len(response.content) > _MAX_ADMIN_RESPONSE_BYTES:
            raise CaddyServiceError("Caddy Admin API response is too large.")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise CaddyServiceError("Failed to parse Caddy Admin API /adapt JSON response.") from exc
        if not isinstance(payload, dict):
            raise CaddyServiceError("Caddy Admin API /adapt response must be a JSON object.")
        return _normalize_adapted_config(payload)

    async def get_config(self) -> dict[str, Any]:
        response = await self._request("GET", "/config/")
        if len(response.content) > _MAX_ADMIN_RESPONSE_BYTES:
            raise CaddyServiceError("Caddy Admin API response is too large.")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise CaddyServiceError("Failed to parse Caddy Admin API JSON response.") from exc
        if not isinstance(payload, dict):
            raise CaddyServiceError("Caddy Admin API response must be a JSON object.")
        return payload

    async def get_version(self) -> str | None:
        """Best-effort Caddy version lookup via the Admin API.

        The Caddy admin API has no dedicated version endpoint — ``GET /`` returns a 404 — so
        this normally yields ``None`` against a real Caddy. Callers must treat a missing
        version as "unknown", not as "not Caddy": a reachable structured admin API (``/config/``
        returning JSON) is itself proof of Caddy 2.x. The parse below is kept for the rare
        builds/proxies that expose a ``Caddy v…`` banner at the root.
        """
        try:
            response = await self._request("GET", "/")
            if len(response.content) > _MAX_ADMIN_RESPONSE_BYTES:
                raise CaddyServiceError("Caddy Admin API response is too large.")
            payload = response.text.strip()
            if payload.startswith("Caddy "):
                parts = payload.split()
                if len(parts) >= 2:
                    return parts[1]  # e.g., "v2.8.4"
            return None
        except CaddyServiceError:
            return None


class CaddyService:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @staticmethod
    def _caddy_scope_name(scope_type: Literal["domain", "wildcard"], scope_name: str) -> str:
        """Return the on-disk storage name Caddy uses for a scope.

        Caddy stores "*.example.com" under the directory "wildcard_.example.com"
        and a plain domain under its bare name.
        """
        normalized = scope_name.strip().lower()
        if not normalized:
            return ""
        if scope_type == "wildcard":
            if not normalized.startswith("*."):
                normalized = f"*.{normalized.lstrip('*.')}"
            return normalized.replace("*", "wildcard_")
        return normalized.removeprefix("*.")

    @staticmethod
    def _path_matches_scope(
        path: Path,
        scope_type: Literal["domain", "wildcard"],
        scope_name: str,
        *,
        root: Path,
        cert_root: Path,
    ) -> bool:
        """Match only the well-known Caddy storage layouts for a given scope.

        Matching is anchored to the storage root and its expected depth so a
        same-named backup/archive directory elsewhere under the root is never
        a deletion target.
        """
        expected_scope = CaddyService._caddy_scope_name(scope_type, scope_name)
        if not expected_scope:
            return False

        try:
            rel_parts = tuple(part.strip().lower() for part in path.relative_to(root).parts)
        except ValueError:
            return False
        if not rel_parts:
            return False

        root_name = root.name.lower()
        if root == cert_root:
            # certificates/<issuer>/<scope>/
            return path.is_dir() and len(rel_parts) == 2 and rel_parts[1] == expected_scope
        if root_name == "ocsp":
            # ocsp/<scope>.ocsp
            return path.is_file() and len(rel_parts) == 1 and rel_parts[0] == f"{expected_scope}.ocsp"
        if root_name == "acme":
            # acme account/order storage: only delete scope directories.
            return path.is_dir() and expected_scope in rel_parts
        return False

    @staticmethod
    def _prune_duplicate_paths(paths: Iterable[Path]) -> list[Path]:
        unique_paths = sorted({path for path in paths}, key=lambda item: (len(item.parts), str(item)))
        pruned: list[Path] = []
        for candidate in unique_paths:
            if any(candidate == parent or candidate.is_relative_to(parent) for parent in pruned):
                continue
            pruned.append(candidate)
        return pruned

    _MIN_CERT_ROOT_DEPTH = 4

    @staticmethod
    def _resolve_certificate_root(certificates_path: Path) -> Path:
        root = certificates_path.expanduser().resolve(strict=False)
        if root.name != "certificates" or len(root.parts) < CaddyService._MIN_CERT_ROOT_DEPTH:
            raise CaddyServiceError(f"Unsafe Caddy certificate storage root: {root}")
        return root

    @staticmethod
    def _is_within_any_root(path: Path, roots: tuple[Path, ...]) -> bool:
        resolved = path.resolve(strict=False)
        return any(resolved == root or resolved.is_relative_to(root) for root in roots)

    @staticmethod
    def _remove_paths(paths: Iterable[Path], *, allowed_roots: tuple[Path, ...] | None = None) -> int:
        # Validate every path before deleting any, so a single out-of-root path
        # cannot leave a partial deletion behind.
        path_list = list(paths)
        if allowed_roots is not None:
            for path in path_list:
                if not CaddyService._is_within_any_root(path, allowed_roots):
                    raise CaddyServiceError(f"Refusing to delete outside certificate roots: {path}")

        removed = 0
        for path in path_list:
            if not path.exists() and not path.is_symlink():
                continue
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=False)
            else:
                path.unlink(missing_ok=True)
            removed += 1
        return removed

    async def purge_certificate_artifacts(
        self,
        domain: str,
        certificates_path: Path | None,
        *,
        scope_type: Literal["domain", "wildcard"] = "domain",
        scope_name: str | None = None,
    ) -> int:
        """Delete host-specific certificate artifacts from Caddy's file-system storage."""
        normalized_scope_name = (scope_name or domain).strip().lower()
        if not normalized_scope_name or certificates_path is None:
            return 0

        try:
            cert_root = self._resolve_certificate_root(certificates_path)
        except CaddyServiceError:
            logger.warning("Refusing certificate purge: unsafe configured root %s", certificates_path)
            raise

        roots: list[Path] = []
        roots.append(cert_root)
        roots.append(cert_root.parent / "acme")
        roots.append(cert_root.parent / "ocsp")
        resolved_roots = tuple(r.resolve(strict=False) for r in roots)

        def collect_matches() -> list[Path]:
            matches: list[Path] = []
            checked = 0
            any_root_accessible = False
            any_root_seen = False
            skipped_inaccessible = False
            for root in roots:
                try:
                    exists = root.exists()
                except PermissionError:
                    any_root_seen = True
                    skipped_inaccessible = True
                    logger.warning("Skipping inaccessible Caddy certificate storage root: %s", root)
                    continue
                except OSError as exc:
                    raise CaddyServiceError(f"Could not scan certificate storage root: {root}") from exc

                if not exists:
                    continue
                any_root_seen = True
                any_root_accessible = True
                try:
                    for path in root.rglob("*"):
                        checked += 1
                        if checked > _MAX_CERT_PURGE_SCAN_PATHS:
                            raise CaddyServiceError("Certificate storage scan limit exceeded.")
                        if not self._path_matches_scope(
                            path,
                            scope_type,
                            normalized_scope_name,
                            root=root,
                            cert_root=cert_root,
                        ):
                            continue
                        matches.append(path)
                except PermissionError:
                    raise CaddyServiceError(f"Could not scan certificate storage root: {root}")
                except OSError as exc:
                    raise CaddyServiceError(f"Could not scan certificate storage root: {root}") from exc
            if not any_root_accessible and not any_root_seen:
                logger.warning(
                    "No accessible certificate storage roots found for domain '%s'. "
                    "Configured path: %s. If running in Docker, mount the Caddy "
                    "certificate storage into the container and set CB_CADDY_CERTIFICATES_PATH.",
                    normalized_scope_name,
                    cert_root,
                )
            pruned = self._prune_duplicate_paths(matches)
            # A skipped (permission-denied) root combined with matches elsewhere
            # would mean a partial, inconsistent purge; refuse rather than delete
            # only some of the scope's artifacts.
            if skipped_inaccessible and pruned:
                raise CaddyServiceError(
                    "Refusing partial certificate purge: some storage roots were inaccessible."
                )
            return pruned

        matched_paths = await asyncio.to_thread(collect_matches)
        if not matched_paths:
            return 0
        return await asyncio.to_thread(self._remove_paths, matched_paths, allowed_roots=resolved_roots)

    async def adapt_caddyfile_to_json(
        self,
        caddyfile: str,
        *,
        admin_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        config, _warnings = await self._adapt_caddyfile_raw(
            caddyfile, admin_url=admin_url, timeout_seconds=timeout_seconds
        )
        return config

    async def adapt_caddyfile_to_json_with_format_check(
        self,
        caddyfile: str,
        *,
        admin_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Return (adapted_config, has_format_warning)."""
        config, warnings = await self._adapt_caddyfile_raw(
            caddyfile, admin_url=admin_url, timeout_seconds=timeout_seconds
        )
        return config, any(_is_format_warning(w) for w in warnings)

    async def _adapt_caddyfile_raw(
        self,
        caddyfile: str,
        *,
        admin_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        _validate_caddyfile_size(caddyfile)
        settings = get_settings()
        base_url = admin_url or settings.caddy_api_url
        timeout = timeout_seconds or settings.caddy_admin_timeout_seconds
        async with CaddyAdminClient(base_url, timeout) as client:
            return await client.adapt_caddyfile(caddyfile)

    async def aclose(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    async def validate_caddyfile(self, caddyfile: str, *, admin_url: str | None = None) -> tuple[bool, str]:
        """Validate a Caddyfile without deploying.

        Returns:
            Tuple of (valid, message).
            On success: (True, "Configuration is valid")
            On failure: (False, error_message)
        """
        try:
            await self.adapt_caddyfile_to_json(caddyfile, admin_url=admin_url)
            return True, "Configuration is valid"
        except CaddyServiceError as exc:
            msg = str(exc)
            if "request failed with status" in msg and "Response body:" in msg:
                # Caddy rejected the Caddyfile — surface its error message.
                body = msg[msg.index("Response body:") + len("Response body: "):]
                logger.warning("Caddyfile rejected by Caddy: %s", body)
                return False, body
            logger.warning("Caddyfile validation failed: %s", exc)
            return False, "Caddy Admin API unavailable. Check your admin URL in Settings."

    async def format_caddyfile(self, caddyfile: str) -> str:
        """Format a Caddyfile with a deterministic local UI formatter.

        Returns:
            Formatted Caddyfile content with proper indentation.

        Raises:
            CaddyServiceError: If formatting fails.
        """
        _validate_caddyfile_size(caddyfile)
        # This is intentionally not a full Caddy formatter; it only normalizes
        # indentation for UI editing when the Caddy CLI is not available.
        return self._format_caddyfile_locally(caddyfile)

    async def format_site_directives(self, directives: str) -> str:
        """Format Caddy directives (site block content) with the local UI formatter.

        Wraps directives in a dummy site block, formats, and extracts the result.

        Returns:
            Formatted directives with proper indentation stripped.

        Raises:
            CaddyServiceError: If formatting fails.
        """
        trimmed = directives.strip()
        if not trimmed:
            return ""

        # Wrap in a dummy site block for formatting
        wrapper = f"format.example {{\n{trimmed}\n}}"
        formatted_full = await self.format_caddyfile(wrapper)

        # Extract content between first { and last }
        lines = formatted_full.strip().splitlines()
        if len(lines) < 2:
            return trimmed

        # Skip first line (site label + {) and last line (})
        inner_lines = lines[1:-1] if lines[-1].strip() == "}" else lines[1:]

        # Remove one level of indentation (Caddy uses tabs)
        result_lines: list[str] = []
        for line in inner_lines:
            if line.startswith("\t"):
                result_lines.append(line[1:])
            elif line.startswith("    "):
                result_lines.append(line[4:])
            else:
                result_lines.append(line)

        return "\n".join(result_lines).strip()

    @staticmethod
    def _format_caddyfile_locally(caddyfile: str) -> str:
        lines: list[str] = []
        indent = 0

        for raw_line in caddyfile.strip().splitlines():
            stripped = raw_line.strip()
            if not stripped:
                lines.append("")
                continue

            if stripped.startswith("}"):
                indent = max(0, indent - 1)

            lines.append(("\t" * indent) + stripped)

            opens = stripped.count("{")
            closes = stripped.count("}")
            indent = max(0, indent + opens - closes)

        return "\n".join(lines).strip() + ("\n" if lines else "")


caddy_service = CaddyService()
