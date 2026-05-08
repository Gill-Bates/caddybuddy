#!/usr/bin/env python3
#
# app/services/caddy.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import json
import socket
import shutil
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from ipaddress import ip_address
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.models.entities import CaddyServer


_FORBIDDEN_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})
_FORBIDDEN_IPS = frozenset({ip_address("169.254.169.254")})
_CADDY_ADAPT_TIMEOUT = 10.0
_CADDY_KILL_TIMEOUT = 2.0


class CaddyServiceError(Exception):
    """Domain exception for Caddy Admin API transport failures."""


class CaddyService:
    _DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
    _LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._caddy_path: str | None = None
        self._checked_caddy = False

    def _find_caddy_binary(self) -> str | None:
        if self._checked_caddy:
            return self._caddy_path

        self._checked_caddy = True
        self._caddy_path = shutil.which("caddy")
        return self._caddy_path

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

        return (
            process.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

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
        with NamedTemporaryFile(
            mode="w",
            suffix=".caddyfile",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(caddyfile)
            tmp_path = Path(tmp.name)

        try:
            return_code, stdout, stderr = await self._run_caddy_command(
                ["adapt", "--config", str(tmp_path), "--adapter", "caddyfile"],
            )
        finally:
            tmp_path.unlink(missing_ok=True)

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
    def _base_url(server: CaddyServer) -> str:
        parsed = urlsplit(server.api_url)
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
        return urlunsplit((parsed.scheme, f"{host}:{server.api_port}", "", "", ""))

    @staticmethod
    def _relative_admin_api_path(server: CaddyServer) -> str:
        path = server.admin_api_path.strip()
        if path.startswith(("http://", "https://", "//")):
            raise ValueError("admin_api_path must be a relative path, not an absolute URL.")
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc:
            raise ValueError("admin_api_path must be a relative path, not an absolute URL.")
        normalized = path.lstrip("/")
        if not normalized:
            raise ValueError("admin_api_path must not be empty.")
        return normalized

    def _endpoint(self, server: CaddyServer) -> str:
        return urljoin(f"{self._base_url(server)}/", self._relative_admin_api_path(server))

    @staticmethod
    def _load_endpoint(server: CaddyServer) -> str:
        return urljoin(f"{CaddyService._base_url(server)}/", "load")

    @staticmethod
    def _normalize_resolved_ip(target_ip):
        return getattr(target_ip, "ipv4_mapped", None) or target_ip

    @classmethod
    def _is_forbidden_resolved_ip(cls, target_ip) -> bool:
        normalized_ip = cls._normalize_resolved_ip(target_ip)
        return normalized_ip.is_link_local or normalized_ip in _FORBIDDEN_IPS

    async def _resolve_target_ips(self, host: str, port: int | None) -> set:
        try:
            return {ip_address(host)}
        except ValueError:
            loop = asyncio.get_running_loop()
            try:
                address_info = await loop.getaddrinfo(
                    host,
                    port,
                    family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                )
            except OSError as exc:
                raise ValueError(f"Failed to resolve Caddy admin host: {host!r}") from exc

        resolved_ips = {
            ip_address(sockaddr[0])
            for *_prefix, sockaddr in address_info
            if sockaddr and sockaddr[0]
        }
        if not resolved_ips:
            raise ValueError(f"Failed to resolve Caddy admin host: {host!r}")
        return resolved_ips

    async def _validate_target(self, server: CaddyServer) -> None:
        parsed = urlsplit(self._base_url(server))
        host = (parsed.hostname or "").lower()
        if host in _FORBIDDEN_HOSTS:
            raise ValueError(f"Blocked Caddy admin target: {host!r}")

        resolved_ips = await self._resolve_target_ips(host, parsed.port)
        for target_ip in resolved_ips:
            if self._is_forbidden_resolved_ip(target_ip):
                raise ValueError(
                    f"Link-local or metadata Caddy admin targets are not allowed: {target_ip!s}"
                )

    async def test_connection(self, server: CaddyServer) -> dict:
        return await self.fetch_config(server)

    async def fetch_config(self, server: CaddyServer) -> dict:
        await self._validate_target(server)
        client = self._get_client()
        try:
            response = await client.get(self._endpoint(server))
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CaddyServiceError(f"Caddy API request failed: {exc}") from exc
        return response.json()

    async def deploy_config(self, server: CaddyServer, config_payload: dict) -> dict:
        await self._validate_target(server)
        client = self._get_client()
        try:
            response = await client.post(self._load_endpoint(server), json=config_payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CaddyServiceError(f"Caddy API request failed: {exc}") from exc
        return response.json() if response.content else {"status": "ok"}

    @staticmethod
    def mark_server_online(server: CaddyServer) -> None:
        server.status = "online"
        server.last_pinged = datetime.now(UTC)

    @staticmethod
    def mark_server_offline(server: CaddyServer) -> None:
        server.status = "offline"
        server.last_pinged = datetime.now(UTC)

    @staticmethod
    def extract_sites(config_payload: dict) -> list[str]:
        apps = config_payload.get("apps")
        if not isinstance(apps, dict):
            return []

        http_app = apps.get("http")
        if not isinstance(http_app, dict):
            return []

        servers = http_app.get("servers")
        if not isinstance(servers, dict):
            return []

        sites: list[str] = []
        seen_sites: set[str] = set()
        for server_body in servers.values():
            if not isinstance(server_body, dict):
                continue
            routes = server_body.get("routes", [])
            if not isinstance(routes, list):
                continue
            for route in routes:
                if not isinstance(route, dict):
                    continue
                matches = route.get("match", [])
                if not isinstance(matches, list):
                    continue
                for match in matches:
                    if not isinstance(match, dict):
                        continue
                    hosts = match.get("host", [])
                    if not isinstance(hosts, list):
                        continue
                    for host in hosts:
                        if not isinstance(host, str) or not host or host in seen_sites:
                            continue
                        seen_sites.add(host)
                        sites.append(host)
        return sites


caddy_service = CaddyService()