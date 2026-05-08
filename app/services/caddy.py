#!/usr/bin/env python3
#
# app/services/caddy.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import copy
import json
import socket
import shutil
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.models.entities import CaddyServer
from app.utils.caddyfile import prepare_domain_directives


_FORBIDDEN_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})
_FORBIDDEN_IPS = frozenset({ip_address("169.254.169.254")})
_CADDY_ADAPT_TIMEOUT = 10.0
_CADDY_KILL_TIMEOUT = 2.0
_SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
type ResolvedIPAddress = IPv4Address | IPv6Address


@dataclass(frozen=True, slots=True)
class ImportedSiteDefinition:
    domain: str
    template_name: str
    caddyfile: str
    upstream: str | None
    ssl_enabled: bool


class CaddyServiceError(Exception):
    """Domain exception for Caddy Admin API transport failures."""


def _format_size_bytes(value: int) -> str:
    if value % 1_000_000 == 0:
        return f"{value // 1_000_000}MB"
    if value % 1_000 == 0:
        return f"{value // 1_000}KB"
    return str(value)


def _format_duration_ns(value: int) -> str:
    if value % 1_000_000_000 == 0:
        return f"{value // 1_000_000_000}s"
    if value % 1_000_000 == 0:
        return f"{value // 1_000_000}ms"
    return f"{value}ns"


def _extract_matched_hosts(route: dict) -> list[str]:
    hosts: list[str] = []
    for match in route.get("match", []):
        if not isinstance(match, dict):
            continue
        route_hosts = match.get("host", [])
        if not isinstance(route_hosts, list):
            continue
        for host in route_hosts:
            if isinstance(host, str) and host:
                hosts.append(host)
    return hosts


def _flatten_route_handlers(route: dict) -> list[dict]:
    handlers: list[dict] = []
    for handler in route.get("handle", []):
        if not isinstance(handler, dict):
            continue
        if handler.get("handler") == "subroute":
            for subroute in handler.get("routes", []):
                if not isinstance(subroute, dict):
                    continue
                for subhandler in subroute.get("handle", []):
                    if isinstance(subhandler, dict):
                        handlers.append(subhandler)
            continue
        handlers.append(handler)
    return handlers


def _extract_logger_directives(host: str, server_body: dict, logging_config: dict) -> tuple[bool, str]:
    server_logs = server_body.get("logs", {})
    logger_names = server_logs.get("logger_names", {}) if isinstance(server_logs, dict) else {}
    host_loggers = logger_names.get(host, []) if isinstance(logger_names, dict) else []
    if not isinstance(host_loggers, list) or not host_loggers:
        return False, ""

    default_log = False
    lines: list[str] = []
    for logger_name in host_loggers:
        if not isinstance(logger_name, str):
            continue
        logger_body = logging_config.get(logger_name, {}) if isinstance(logging_config, dict) else {}
        if not isinstance(logger_body, dict):
            continue
        writer = logger_body.get("writer", {})
        encoder = logger_body.get("encoder", {})
        filename = writer.get("filename") if isinstance(writer, dict) else None
        encoder_format = encoder.get("format") if isinstance(encoder, dict) else None

        if filename == "/var/log/caddy/access.log" and encoder_format == "json":
            default_log = True
            continue

        if isinstance(filename, str) and filename:
            roll_size_mb = writer.get("roll_size_mb") if isinstance(writer, dict) else None
            if isinstance(roll_size_mb, int) and roll_size_mb > 0:
                lines.extend([
                    f"output file {filename} {{",
                    f"    roll_size {roll_size_mb}mb",
                    "}",
                ])
            else:
                lines.append(f"output file {filename}")
        if encoder_format == "json":
            lines.append("format json")

    return default_log, "\n".join(lines).strip()


def _build_imported_site_definition(
    host: str,
    handlers: list[dict],
    server_body: dict,
    logging_config: dict,
    *,
    template_name_prefix: str,
) -> ImportedSiteDefinition | None:
    upstream: str | None = None
    reverse_proxy_options: list[str] = []
    encode_directives = ""
    header_lines: list[str] = []
    request_body_directives = ""
    log_directives = ""
    imports: list[str] = []
    pending_security_headers = dict(_SECURITY_HEADERS)

    default_log, extracted_log_directives = _extract_logger_directives(host, server_body, logging_config)
    if default_log:
        imports.append("default_log")
    elif extracted_log_directives:
        log_directives = extracted_log_directives

    for handler in handlers:
        handler_name = handler.get("handler")

        if handler_name == "headers":
            response = handler.get("response", {})
            if isinstance(response, dict):
                set_headers = response.get("set", {})
                if isinstance(set_headers, dict):
                    for header_name, values in set_headers.items():
                        if not isinstance(header_name, str) or not isinstance(values, list) or len(values) != 1:
                            continue
                        header_value = values[0]
                        if not isinstance(header_value, str):
                            continue
                        if pending_security_headers.get(header_name) == header_value:
                            pending_security_headers.pop(header_name, None)
                            continue
                        header_lines.append(f'{header_name} "{header_value}"')

                deleted_headers = response.get("delete", [])
                if isinstance(deleted_headers, list):
                    for header_name in deleted_headers:
                        if isinstance(header_name, str) and header_name:
                            header_lines.append(f"-{header_name}")
            continue

        if handler_name == "request_body":
            max_size = handler.get("max_size")
            if isinstance(max_size, int) and max_size > 0:
                request_body_directives = f"max_size {_format_size_bytes(max_size)}"
            continue

        if handler_name == "encode":
            encoding_names: list[str] = []
            prefer = handler.get("prefer", [])
            if isinstance(prefer, list):
                encoding_names.extend(name for name in prefer if isinstance(name, str))
            encodings = handler.get("encodings", {})
            if isinstance(encodings, dict):
                for name in encodings:
                    if isinstance(name, str) and name not in encoding_names:
                        encoding_names.append(name)
            encode_directives = " ".join(encoding_names)
            continue

        if handler_name == "reverse_proxy":
            upstreams = handler.get("upstreams", [])
            if isinstance(upstreams, list) and len(upstreams) == 1 and isinstance(upstreams[0], dict):
                dial = upstreams[0].get("dial")
                if isinstance(dial, str) and dial:
                    upstream = dial

            transport = handler.get("transport", {})
            if isinstance(transport, dict) and transport.get("protocol") == "http":
                keep_alive = transport.get("keep_alive", {})
                if isinstance(keep_alive, dict):
                    idle_timeout = keep_alive.get("idle_timeout")
                    if isinstance(idle_timeout, int) and idle_timeout > 0:
                        reverse_proxy_options.extend([
                            "transport http {",
                            f"    keepalive {_format_duration_ns(idle_timeout)}",
                            "}",
                        ])

            request_headers = handler.get("headers", {}).get("request", {}).get("set", {})
            if isinstance(request_headers, dict):
                for header_name, values in request_headers.items():
                    if not isinstance(header_name, str) or not isinstance(values, list) or len(values) != 1:
                        continue
                    header_value = values[0]
                    if not isinstance(header_value, str):
                        continue
                    if header_name == "Host" and header_value == "{http.request.host}":
                        header_value = "{host}"
                    reverse_proxy_options.append(f"header_up {header_name} {header_value}")
            continue

    if not pending_security_headers:
        imports.insert(0, "security_headers")

    prepared = prepare_domain_directives(
        upstream="{{upstream}}" if upstream else None,
        reverse_proxy_options="\n".join(reverse_proxy_options),
        encode_directives=encode_directives,
        header_directives="\n".join(header_lines),
        request_body_directives=request_body_directives,
        log_directives=log_directives,
        tls_directives="",
        basic_auth_directives="",
        custom_directives="",
    )
    if prepared.errors:
        return None

    directives = prepared.caddy_directives or ""
    if imports:
        directives = "\n\n".join([*(f"import {name}" for name in imports), directives]).strip()
    if not directives:
        return None

    listeners = server_body.get("listen", [])
    ssl_enabled = True
    if isinstance(listeners, list) and listeners:
        normalized_listeners = {listener for listener in listeners if isinstance(listener, str)}
        ssl_enabled = ":443" in normalized_listeners or all(listener != ":80" for listener in normalized_listeners)

    return ImportedSiteDefinition(
        domain=host,
        template_name=f"{template_name_prefix} ({host})",
        caddyfile=directives,
        upstream=upstream,
        ssl_enabled=ssl_enabled,
    )


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
        if not isinstance(server.api_port, int) or not 1 <= server.api_port <= 65535:
            raise ValueError("api_port must be a valid integer port between 1 and 65535.")

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
        path = server.admin_api_path
        if not isinstance(path, str):
            raise ValueError("admin_api_path must be a string.")

        path = path.strip()
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
    def _normalize_resolved_ip(target_ip: ResolvedIPAddress) -> ResolvedIPAddress:
        return getattr(target_ip, "ipv4_mapped", None) or target_ip

    @classmethod
    def _is_forbidden_resolved_ip(cls, target_ip: ResolvedIPAddress) -> bool:
        normalized_ip = cls._normalize_resolved_ip(target_ip)
        return normalized_ip.is_link_local or normalized_ip in _FORBIDDEN_IPS

    async def _resolve_target_ips(self, host: str, port: int | None) -> set[ResolvedIPAddress]:
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
        # This is a best-effort SSRF guard. httpx resolves DNS again when opening
        # the socket, so a hostile resolver can still exploit a DNS TOCTOU gap.
        # Keep this limitation documented here unless request transport is pinned
        # to the validated IP address explicitly.
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

    @staticmethod
    def _server_listen_key(server_body: dict) -> tuple[str, ...]:
        listeners = server_body.get("listen", [])
        if not isinstance(listeners, list):
            return ()
        return tuple(sorted(listener for listener in listeners if isinstance(listener, str)))

    @staticmethod
    def _remove_managed_routes(server_body: dict, managed_domains: set[str]) -> None:
        routes = server_body.get("routes", [])
        if isinstance(routes, list):
            server_body["routes"] = [
                route
                for route in routes
                if managed_domains.isdisjoint(_extract_matched_hosts(route) if isinstance(route, dict) else [])
            ]

        logs = server_body.get("logs")
        if not isinstance(logs, dict):
            return
        logger_names = logs.get("logger_names")
        if not isinstance(logger_names, dict):
            return

        filtered_logger_names = {
            host: names
            for host, names in logger_names.items()
            if isinstance(host, str) and host not in managed_domains
        }
        if filtered_logger_names:
            logs["logger_names"] = filtered_logger_names
            return
        logs.pop("logger_names", None)
        if not logs:
            server_body.pop("logs", None)

    @classmethod
    def merge_managed_config(
        cls,
        existing_config: dict,
        managed_config: dict,
        *,
        managed_domains: set[str],
    ) -> dict:
        if not managed_domains:
            return copy.deepcopy(managed_config)

        merged_config = copy.deepcopy(existing_config)
        merged_apps = merged_config.setdefault("apps", {})
        managed_apps = managed_config.get("apps", {})
        if not isinstance(merged_apps, dict) or not isinstance(managed_apps, dict):
            return copy.deepcopy(managed_config)

        managed_http = managed_apps.get("http")
        if not isinstance(managed_http, dict):
            return merged_config

        existing_http = merged_apps.get("http")
        if not isinstance(existing_http, dict):
            merged_apps["http"] = copy.deepcopy(managed_http)
        else:
            existing_servers = existing_http.setdefault("servers", {})
            if not isinstance(existing_servers, dict):
                existing_servers = {}
                existing_http["servers"] = existing_servers

            for server_body in existing_servers.values():
                if isinstance(server_body, dict):
                    cls._remove_managed_routes(server_body, managed_domains)

            managed_servers = managed_http.get("servers", {})
            if isinstance(managed_servers, dict):
                for managed_name, managed_server in managed_servers.items():
                    if not isinstance(managed_server, dict):
                        continue
                    target_server_name = next(
                        (
                            existing_name
                            for existing_name, existing_server in existing_servers.items()
                            if isinstance(existing_server, dict)
                            and cls._server_listen_key(existing_server) == cls._server_listen_key(managed_server)
                        ),
                        None,
                    )
                    if target_server_name is None:
                        candidate_name = managed_name
                        suffix = 1
                        while candidate_name in existing_servers:
                            candidate_name = f"{managed_name}_{suffix}"
                            suffix += 1
                        existing_servers[candidate_name] = copy.deepcopy(managed_server)
                        continue

                    target_server = existing_servers[target_server_name]
                    target_routes = target_server.setdefault("routes", [])
                    managed_routes = managed_server.get("routes", [])
                    if isinstance(target_routes, list) and isinstance(managed_routes, list):
                        target_routes.extend(copy.deepcopy(managed_routes))

                    managed_logs = managed_server.get("logs")
                    if isinstance(managed_logs, dict):
                        target_logs = target_server.setdefault("logs", {})
                        if isinstance(target_logs, dict):
                            managed_logger_names = managed_logs.get("logger_names")
                            if isinstance(managed_logger_names, dict):
                                target_logger_names = target_logs.setdefault("logger_names", {})
                                if isinstance(target_logger_names, dict):
                                    for host, names in managed_logger_names.items():
                                        if isinstance(host, str):
                                            target_logger_names[host] = copy.deepcopy(names)

        managed_logging = managed_config.get("logging")
        if isinstance(managed_logging, dict):
            merged_logging = merged_config.setdefault("logging", {})
            if isinstance(merged_logging, dict):
                merged_logs = merged_logging.setdefault("logs", {})
                managed_logs = managed_logging.get("logs", {})
                if isinstance(merged_logs, dict) and isinstance(managed_logs, dict):
                    for log_name, log_config in managed_logs.items():
                        merged_logs[log_name] = copy.deepcopy(log_config)

        return merged_config

    @staticmethod
    def extract_site_definitions(
        config_payload: dict,
        *,
        template_name_prefix: str,
    ) -> list[ImportedSiteDefinition]:
        apps = config_payload.get("apps")
        if not isinstance(apps, dict):
            return []

        http_app = apps.get("http")
        if not isinstance(http_app, dict):
            return []

        servers = http_app.get("servers")
        if not isinstance(servers, dict):
            return []

        logging_config = config_payload.get("logging", {}).get("logs", {})
        imported_sites: list[ImportedSiteDefinition] = []
        seen_domains: set[str] = set()

        for server_body in servers.values():
            if not isinstance(server_body, dict):
                continue
            routes = server_body.get("routes", [])
            if not isinstance(routes, list):
                continue
            for route in routes:
                if not isinstance(route, dict):
                    continue
                hosts = _extract_matched_hosts(route)
                if not hosts:
                    continue
                handlers = _flatten_route_handlers(route)
                if not handlers:
                    continue
                for host in hosts:
                    if host in seen_domains:
                        continue
                    imported_site = _build_imported_site_definition(
                        host,
                        handlers,
                        server_body,
                        logging_config if isinstance(logging_config, dict) else {},
                        template_name_prefix=template_name_prefix,
                    )
                    if imported_site is None:
                        continue
                    seen_domains.add(host)
                    imported_sites.append(imported_site)

        return imported_sites


caddy_service = CaddyService()