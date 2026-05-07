#!/usr/bin/env python3
#
# app/services/caddy.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime
from ipaddress import ip_address
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.models.entities import CaddyServer


_FORBIDDEN_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})
_FORBIDDEN_IPS = frozenset({ip_address("169.254.169.254")})


class CaddyService:
    _DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
    _LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

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

    def _endpoint(self, server: CaddyServer) -> str:
        return urljoin(f"{self._base_url(server)}/", server.admin_api_path.lstrip("/"))

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
        response = await client.get(self._endpoint(server))
        response.raise_for_status()
        return response.json()

    async def deploy_config(self, server: CaddyServer, config_payload: dict) -> dict:
        await self._validate_target(server)
        client = self._get_client()
        response = await client.post(self._load_endpoint(server), json=config_payload)
        response.raise_for_status()
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