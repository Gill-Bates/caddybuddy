#!/usr/bin/env python3
#
# app/middleware/security.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations


from starlette.types import ASGIApp, Message, Receive, Scope, Send


_SECURITY_HEADERS = (
    (
        b"content-security-policy",
        b"default-src 'self'; style-src 'self'; "
        b"script-src 'self'; font-src 'self' data:; img-src 'self' data: https:; "
        b"connect-src 'self'; object-src 'none'; frame-ancestors 'none'; "
        b"base-uri 'self'; form-action 'self'",
    ),
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)


class SecurityHeadersMiddleware:
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
                for key, value in _SECURITY_HEADERS:
                    if key not in existing_header_names:
                        headers.append((key, value))
                message = {**message, "headers": headers}

            await send(message)

        await self.app(scope, receive, send_with_security_headers)