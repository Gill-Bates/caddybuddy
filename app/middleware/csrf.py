#!/usr/bin/env python3
#
# app/middleware/csrf.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""CSRF protection middleware for UI routes."""

from __future__ import annotations

import posixpath
from functools import cache
from urllib.parse import parse_qs
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config.settings import get_settings
from app.dependencies.web import ensure_csrf_token, validate_csrf_token

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# UI/API path prefixes that require CSRF protection.
# API enforcement is only applied for cookie-authenticated browser requests.
CSRF_PREFIXES = ("/ui/", "/login", "/api/")
_CSRF_EXEMPT_API_PATHS = frozenset({
    "/api/login",
    "/api/mfa/verify",
    "/api/passkeys/login/start",
    "/api/passkeys/login/finish",
})
_SECURITY_HEADERS = (
    (
        b"content-security-policy",
        b"default-src 'self'; style-src 'self'; "
        b"script-src 'self'; font-src 'self' data:; img-src 'self' data: https:; "
        b"connect-src 'self'; object-src 'none'; frame-ancestors 'none'; "
        b"base-uri 'self'; form-action 'self'",
    ),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
)
_HSTS_HEADER = (b"strict-transport-security", b"max-age=63072000; includeSubDomains")


@cache
def _auth_cookie_names() -> tuple[str, ...]:
    return (get_settings().session_cookie_name,)


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


_LOCALHOST_ALIASES = frozenset({"localhost", "127.0.0.1", "::1"})


def _normalize_host(host: str) -> str:
    """Normalize localhost aliases for comparison."""
    return "localhost" if host in _LOCALHOST_ALIASES else host


def _is_bearer_request(request: Request) -> bool:
    """Return True when Authorization header uses Bearer scheme."""
    auth = request.headers.get("Authorization", "").strip()
    scheme, _, _token = auth.partition(" ")
    return scheme.lower() == "bearer"


def _is_https_request(scope: Scope) -> bool:
    return scope.get("scheme") == "https"


def _request_origin_tuple(request: Request) -> tuple[str, str, int] | None:
    scheme = (request.url.scheme or "").lower()
    # When uvicorn doesn't fully trust the reverse proxy's IP (e.g. Docker bridge
    # NAT makes the source appear as 172.17.0.1 instead of 127.0.0.1), it won't
    # rewrite the scope scheme from X-Forwarded-Proto. Fall back to the header
    # directly so the CSRF origin check works behind a TLS-terminating proxy.
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").lower().strip()
    if forwarded_proto in {"http", "https"}:
        scheme = forwarded_proto
    host = request.url.hostname
    if not scheme or not host:
        return None
    normalized_host = _normalize_host(host.lower())
    return (scheme, normalized_host, request.url.port or _default_port(scheme))


def _parse_origin(value: str) -> tuple[str, str, int] | None:
    parsed = urlsplit(value)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname
    if not scheme or not host or parsed.username or parsed.password:
        return None
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    return (scheme, _normalize_host(host.lower()), parsed.port or _default_port(scheme))


class SecurityHeadersMiddleware:
    """Add security headers to HTTP responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing_header_names = {
                    key.lower()
                    for key, _value in message.get("headers", [])
                }
                headers = list(message.get("headers", []))
                headers_to_apply = list(_SECURITY_HEADERS)
                if _is_https_request(scope):
                    headers_to_apply.append(_HSTS_HEADER)

                for key, value in headers_to_apply:
                    if key not in existing_header_names:
                        headers.append((key, value))
                message = {**message, "headers": headers}

            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class CSRFMiddleware:
    """Session-bound CSRF protection for UI and cookie-authenticated API routes."""

    def __init__(self, app: ASGIApp):
        self.app = app

    def _requires_csrf(self, path: str) -> bool:
        """Check if path requires CSRF protection."""
        normalized = posixpath.normpath(path).lower()

        for prefix in CSRF_PREFIXES:
            norm_prefix = posixpath.normpath(prefix).lower()
            if normalized.startswith(norm_prefix + "/") or normalized == norm_prefix:
                return True
        return False

    def _is_cookie_authenticated_api_request(self, request: Request) -> bool:
        """Return True when API request carries session-auth cookie(s)."""
        if not request.url.path.startswith("/api/"):
            return True
        if request.url.path in _CSRF_EXEMPT_API_PATHS:
            return False
        for cookie_name in _auth_cookie_names():
            if request.cookies.get(cookie_name):
                return True
        return False

    def _has_auth_cookie(self, request: Request) -> bool:
        for cookie_name in _auth_cookie_names():
            if request.cookies.get(cookie_name):
                return True
        return False

    def _check_origin(self, request: Request) -> bool:
        """Validate Origin against the resolved request URL."""
        origin = request.headers.get("Origin")
        if not origin:
            return True

        try:
            parsed_origin = _parse_origin(origin)
        except ValueError:
            return False
        request_origin = _request_origin_tuple(request)
        if parsed_origin is None or request_origin is None:
            return False
        return parsed_origin == request_origin

    @staticmethod
    def _body_replay_receive(body: bytes, original_receive: Receive) -> Receive:
        body_sent = False

        async def _receive() -> Message:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }
            return await original_receive()

        return _receive

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        body_receive = receive
        validation_response: Response | None = None

        if request.method in SAFE_METHODS and self._requires_csrf(request.url.path):
            ensure_csrf_token(request)

        is_stateless_bearer_api_request = (
            request.url.path.startswith("/api/")
            and _is_bearer_request(request)
            and not self._has_auth_cookie(request)
        )

        # Validate unsafe methods on protected paths.
        if (
            request.method not in SAFE_METHODS
            and self._requires_csrf(request.url.path)
            and not is_stateless_bearer_api_request
            and self._is_cookie_authenticated_api_request(request)
        ):
            if not self._check_origin(request):
                validation_response = JSONResponse(
                    content={"detail": "Cross-origin request blocked"},
                    status_code=403,
                )
            else:
                submitted_token = request.headers.get("X-CSRF-Token")
                if not submitted_token:
                    content_type = request.headers.get("Content-Type", "")
                    if "application/x-www-form-urlencoded" in content_type:
                        try:
                            raw_body = await request.body()
                            body_receive = self._body_replay_receive(raw_body, receive)
                            parsed = parse_qs(
                                raw_body.decode("utf-8", errors="ignore"),
                                keep_blank_values=True,
                            )
                            submitted_token = parsed.get("csrf_token", [None])[0]
                        except (ClientDisconnect, RuntimeError):
                            submitted_token = None

                try:
                    validate_csrf_token(request, submitted_token)
                except HTTPException:
                    validation_response = JSONResponse(
                        content={"detail": "CSRF token missing or invalid"},
                        status_code=403,
                    )

        if validation_response is not None:
            await validation_response(scope, body_receive, send)
            return

        await self.app(scope, body_receive, send)
