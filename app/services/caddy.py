#!/usr/bin/env python3
#
# app/services/caddy.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import shutil
from collections.abc import Iterable
from contextlib import suppress
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from tempfile import mkstemp
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

_ALLOWED_ADMIN_HOSTS = frozenset({"localhost", "host.docker.internal", "caddy"})
_FORBIDDEN_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})
_FORBIDDEN_IPS = frozenset({ip_address("169.254.169.254")})
_CADDY_ADAPT_TIMEOUT = 10.0
_CADDY_KILL_TIMEOUT = 2.0
_DNS_RESOLUTION_TIMEOUT = 5.0
_MAX_CADDYFILE_BYTES = 512 * 1024
_MAX_CERT_PURGE_SCAN_PATHS = 20_000
type ResolvedIPAddress = IPv4Address | IPv6Address


logger = logging.getLogger(__name__)


class CaddyServiceError(Exception):
    """Domain exception for Caddy Admin API transport failures."""


def _normalize_resolved_ip(target_ip: ResolvedIPAddress) -> ResolvedIPAddress:
    return getattr(target_ip, "ipv4_mapped", None) or target_ip


def _is_allowed_admin_ip(target_ip: ResolvedIPAddress) -> bool:
    normalized_ip = _normalize_resolved_ip(target_ip)
    if normalized_ip in _FORBIDDEN_IPS:
        return False
    if (
        normalized_ip.is_link_local
        or normalized_ip.is_multicast
        or normalized_ip.is_unspecified
        or normalized_ip.is_reserved
    ):
        return False
    return normalized_ip.is_loopback or normalized_ip.is_private


def _validate_admin_host(host: str) -> None:
    normalized_host = host.strip().lower()
    if normalized_host in _FORBIDDEN_HOSTS:
        raise ValueError(f"Blocked Caddy admin target: {normalized_host!r}")

    try:
        target_ip = ip_address(normalized_host)
    except ValueError:
        if normalized_host not in _ALLOWED_ADMIN_HOSTS:
            raise ValueError(f"Caddy admin host is not allowed: {normalized_host!r}") from None
        return

    if not _is_allowed_admin_ip(target_ip):
        raise ValueError(f"Caddy admin IP target is not allowed: {target_ip!s}")


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


def _normalize_admin_api_path(path: str) -> str:
    normalized = path.strip() or "/load"
    if not normalized.startswith("/"):
        raise ValueError("admin_api_path must start with '/'.")
    if "?" in normalized or "#" in normalized:
        raise ValueError("admin_api_path must not include query or fragment.")
    if normalized != "/load":
        raise ValueError("Only the Caddy /load endpoint is supported.")
    return normalized


def _validate_caddyfile_size(caddyfile: str) -> None:
    if len(caddyfile.encode("utf-8")) > _MAX_CADDYFILE_BYTES:
        raise CaddyServiceError(f"Caddyfile exceeds the {_MAX_CADDYFILE_BYTES} byte limit.")


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
        if not parsed.hostname:
            raise ValueError("Caddy admin URL must include a host.")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Caddy admin URL must not include a path, query, or fragment.")

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        return urlunsplit((parsed.scheme, f"{host}:{port}", "", "", ""))

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, limits=self._LIMITS)
        return self._client

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
        merged = {"Host": _authority_header(host, port)}
        if extra_headers:
            merged.update(extra_headers)
        return merged

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        parsed = urlsplit(self._base_url)
        host = (parsed.hostname or "").lower()
        extra_headers = kwargs.pop("headers", None)

        try:
            _validate_admin_host(host)
            resolved_ips = await _resolve_target_ips(host, parsed.port)
            validated_ip: ResolvedIPAddress | None = None
            for target_ip in resolved_ips:
                if not _is_allowed_admin_ip(target_ip):
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
            response = await self._get_client().request(
                method,
                endpoint,
                headers=self._merge_headers(extra_headers, parsed.hostname or host, parsed.port),
                **kwargs,
            )
            response.raise_for_status()
            return response
        except CaddyServiceError:
            raise
        except httpx.TimeoutException as exc:
            raise CaddyServiceError("Caddy Admin API request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise CaddyServiceError(
                f"Caddy Admin API request failed with status {exc.response.status_code}."
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

    async def get_config(self) -> dict[str, Any]:
        response = await self._request("GET", "/config/")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise CaddyServiceError("Failed to parse Caddy Admin API JSON response.") from exc
        if not isinstance(payload, dict):
            raise CaddyServiceError("Caddy Admin API response must be a JSON object.")
        return payload

    async def get_version(self) -> str | None:
        """Fetch Caddy version from the Admin API root endpoint."""
        try:
            response = await self._request("GET", "/")
            payload = response.text.strip()
            # Root endpoint returns plain text like "Caddy v2.8.4 h1:..."
            if payload.startswith("Caddy "):
                parts = payload.split()
                if len(parts) >= 2:
                    return parts[1]  # e.g., "v2.8.4"
            return None
        except CaddyServiceError:
            return None


class CaddyService:
    _DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
    _LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._caddy_path: str | None = None
        self._deploy_lock = asyncio.Lock()

    def _find_caddy_binary(self) -> str | None:
        if self._caddy_path is not None:
            return self._caddy_path

        caddy_path = shutil.which("caddy")
        if caddy_path is not None:
            self._caddy_path = caddy_path
        return caddy_path

    @staticmethod
    def _path_matches_domain(path: Path, domain: str) -> bool:
        normalized_domain = domain.strip().lower()
        if not normalized_domain:
            return False

        for part in path.parts:
            normalized_part = part.strip().lower()
            if normalized_part == normalized_domain:
                return True

        normalized_name = path.name.strip().lower()
        normalized_stem = path.stem.strip().lower()
        return (
            normalized_name == normalized_domain
            or normalized_stem == normalized_domain
            or normalized_name.startswith(f"{normalized_domain}.")
        )

    @staticmethod
    def _prune_duplicate_paths(paths: Iterable[Path]) -> list[Path]:
        unique_paths = sorted({path for path in paths}, key=lambda item: (len(item.parts), str(item)))
        pruned: list[Path] = []
        for candidate in unique_paths:
            if any(candidate == parent or candidate.is_relative_to(parent) for parent in pruned):
                continue
            pruned.append(candidate)
        return pruned

    @staticmethod
    def _remove_paths(paths: Iterable[Path]) -> int:
        removed = 0
        for path in paths:
            if not path.exists():
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=False)
            else:
                path.unlink(missing_ok=True)
            removed += 1
        return removed

    async def purge_certificate_artifacts(self, domain: str, certificates_path: Path | None) -> int:
        """Delete host-specific certificate artifacts from Caddy's file-system storage."""
        normalized_domain = domain.strip().lower()
        if not normalized_domain or certificates_path is None:
            return 0

        roots: list[Path] = []
        cert_root = certificates_path.expanduser()
        roots.append(cert_root)
        roots.append(cert_root.parent / "acme")
        roots.append(cert_root.parent / "ocsp")

        def collect_matches() -> list[Path]:
            matches: list[Path] = []
            checked = 0
            for root in roots:
                try:
                    if not root.exists():
                        continue
                    for path in root.rglob("*"):
                        checked += 1
                        if checked > _MAX_CERT_PURGE_SCAN_PATHS:
                            raise CaddyServiceError("Certificate storage scan limit exceeded.")
                        if not self._path_matches_domain(path, normalized_domain):
                            continue
                        if path.is_dir():
                            matches.append(path)
                        elif path.parent.name.strip().lower() == normalized_domain:
                            matches.append(path.parent)
                        else:
                            matches.append(path)
                except PermissionError:
                    logger.warning("Skipping inaccessible Caddy certificate storage root: %s", root)
            return self._prune_duplicate_paths(matches)

        matched_paths = await asyncio.to_thread(collect_matches)
        if not matched_paths:
            return 0
        return await asyncio.to_thread(self._remove_paths, matched_paths)

    @staticmethod
    async def _kill_process(process: asyncio.subprocess.Process) -> None:
        with suppress(ProcessLookupError):
            process.kill()

        try:
            await asyncio.wait_for(process.wait(), timeout=_CADDY_KILL_TIMEOUT)
        except asyncio.TimeoutError:
            return

    async def _run_caddy_command(
        self,
        args: list[str],
        *,
        timeout: float = _CADDY_ADAPT_TIMEOUT,
    ) -> tuple[int, str, str]:
        caddy_path = self._find_caddy_binary()
        if not caddy_path:
            raise CaddyServiceError("Caddy binary not available for deployment.")

        process = await asyncio.create_subprocess_exec(
            caddy_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            await self._kill_process(process)
            raise CaddyServiceError(f"Caddy command timed out after {timeout}s") from exc
        except BaseException:
            await self._kill_process(process)
            raise

        return (
            process.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    @staticmethod
    def _write_temp_caddyfile(caddyfile: str) -> Path:
        fd, raw_path = mkstemp(suffix=".caddyfile", text=True)
        path = Path(raw_path)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                tmp.write(caddyfile)
            return path
        except Exception:
            with suppress(OSError):
                os.close(fd)
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _parse_caddy_errors(stderr: str) -> tuple[str, ...]:
        if not stderr.strip():
            return ()

        errors: list[str] = []
        for line in stderr.strip().splitlines():
            normalized = line.strip()
            if not normalized or normalized.startswith("Warning:"):
                continue
            if normalized.startswith("Error:"):
                normalized = normalized[6:].strip()
            elif normalized.startswith("adapt:"):
                normalized = normalized[6:].strip()
            errors.append(normalized)
        return tuple(errors) if errors else (stderr.strip(),)

    async def adapt_caddyfile_to_json(self, caddyfile: str) -> dict:
        _validate_caddyfile_size(caddyfile)
        tmp_path = await asyncio.to_thread(self._write_temp_caddyfile, caddyfile)

        try:
            return_code, stdout, stderr = await self._run_caddy_command(
                ["adapt", "--config", str(tmp_path), "--adapter", "caddyfile"],
            )
        finally:
            await asyncio.to_thread(tmp_path.unlink, missing_ok=True)

        if return_code != 0:
            error_text = "\n".join(self._parse_caddy_errors(stderr)) or "Failed to adapt Caddyfile."
            raise CaddyServiceError(error_text)

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise CaddyServiceError(f"Failed to parse adapted Caddy JSON output: {exc}") from exc

        if not isinstance(payload, dict):
            raise CaddyServiceError("Adapted Caddy configuration must be a JSON object.")

        return payload

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._DEFAULT_TIMEOUT,
                limits=self._LIMITS,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    @staticmethod
    def _settings_base_url(api_url: str, api_port: int) -> str:
        if not isinstance(api_port, int) or not 1 <= api_port <= 65535:
            raise ValueError("api_port must be a valid integer port between 1 and 65535.")

        parsed = urlsplit(api_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"Unsupported Caddy admin scheme: {parsed.scheme!r}")
        if not parsed.hostname:
            raise ValueError("Caddy admin URL must include a host.")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Caddy admin URL must not include a path, query, or fragment.")
        if parsed.port is not None:
            raise ValueError("Caddy admin URL must not include a port; use the dedicated API port field.")

        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        return urlunsplit((parsed.scheme, f"{host}:{api_port}", "", "", ""))

    @staticmethod
    def _request_host_header(api_url: str, api_port: int) -> str:
        parsed = urlsplit(CaddyService._settings_base_url(api_url, api_port))
        if not parsed.hostname:
            raise ValueError("Caddy admin URL must include a host.")
        return _authority_header(parsed.hostname, parsed.port)

    async def _resolve_target_ips(self, host: str, port: int | None) -> list[ResolvedIPAddress]:
        return await _resolve_target_ips(host, port)

    async def _validate_settings_target(self, api_url: str, api_port: int) -> ResolvedIPAddress:
        parsed = urlsplit(self._settings_base_url(api_url, api_port))
        host = (parsed.hostname or "").lower()
        _validate_admin_host(host)

        resolved_ips = await self._resolve_target_ips(host, parsed.port)
        validated_ip: ResolvedIPAddress | None = None
        for target_ip in resolved_ips:
            if not _is_allowed_admin_ip(target_ip):
                raise ValueError(
                    f"Only loopback or private Caddy admin targets are allowed: {target_ip!s}"
                )
            if validated_ip is None:
                validated_ip = target_ip

        if validated_ip is None:
            raise ValueError(f"Failed to resolve Caddy admin host: {host!r}")
        return validated_ip

    async def validate_and_deploy_caddyfile(
        self,
        caddyfile: str,
        *,
        api_url: str,
        api_port: int,
        admin_api_path: str,
    ) -> tuple[bool, str]:
        """Validate and deploy a Caddyfile in one operation.

        Returns:
            Tuple of (success, message).
            On success: (True, "Configuration deployed successfully")
            On failure: (False, error_message)
        """
        async with self._deploy_lock:
            try:
                config_payload = await self.adapt_caddyfile_to_json(caddyfile)
            except CaddyServiceError as exc:
                return False, f"Validation failed: {exc}"

            try:
                validated_ip = await self._validate_settings_target(api_url, api_port)
                load_path = _normalize_admin_api_path(admin_api_path)
                base_url = self._settings_base_url(api_url, api_port)
                parsed = urlsplit(base_url)
                host_header = self._request_host_header(api_url, api_port)
                validated_host = str(validated_ip)
                if ":" in validated_host:
                    pinned_host = f"[{validated_ip}]:{parsed.port}"
                else:
                    pinned_host = f"{validated_ip}:{parsed.port}"
                endpoint = urlunsplit((parsed.scheme, pinned_host, load_path, "", ""))
                headers = {"Host": host_header}
                client = self._get_client()
                response = await client.post(endpoint, json=config_payload, headers=headers)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                return False, f"Deployment failed with HTTP {exc.response.status_code}."
            except httpx.TimeoutException:
                return False, "Deployment failed: Caddy Admin API request timed out."
            except httpx.HTTPError:
                return False, "Deployment failed: Caddy Admin API unavailable."
            except ValueError as exc:
                return False, f"Configuration error: {exc}"
            except CaddyServiceError as exc:
                return False, f"Deployment failed: {exc}"

        return True, "Configuration deployed successfully"

    async def validate_caddyfile(self, caddyfile: str) -> tuple[bool, str]:
        """Validate a Caddyfile without deploying.

        Returns:
            Tuple of (valid, message).
            On success: (True, "Configuration is valid")
            On failure: (False, error_message)
        """
        try:
            await self.adapt_caddyfile_to_json(caddyfile)
            return True, "Configuration is valid"
        except CaddyServiceError as exc:
            return False, str(exc)

    async def format_caddyfile(self, caddyfile: str) -> str:
        """Format a Caddyfile using Caddy's built-in formatter.

        Returns:
            Formatted Caddyfile content with proper indentation.

        Raises:
            CaddyServiceError: If formatting fails.
        """
        _validate_caddyfile_size(caddyfile)
        tmp_path = await asyncio.to_thread(self._write_temp_caddyfile, caddyfile)

        try:
            return_code, stdout, stderr = await self._run_caddy_command(
                ["fmt", str(tmp_path)],
            )
        finally:
            await asyncio.to_thread(tmp_path.unlink, missing_ok=True)

        if return_code != 0:
            error_text = "\n".join(self._parse_caddy_errors(stderr)) or "Failed to format Caddyfile."
            raise CaddyServiceError(error_text)

        return stdout

    async def format_site_directives(self, directives: str) -> str:
        """Format Caddy directives (site block content) using Caddy's formatter.

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


caddy_service = CaddyService()