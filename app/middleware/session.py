#!/usr/bin/env python3
#
# app/middleware/session.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Session middleware with request-aware Secure cookie handling."""

from __future__ import annotations

import json
from base64 import b64decode, b64encode
from typing import Any, Iterable, Literal, Mapping

import itsdangerous
from starlette.datastructures import MutableHeaders
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _TrackedSession(dict[str, Any]):
    """Minimal dict-compatible session that tracks access and mutation."""

    def __init__(self, initial: Mapping[str, Any] | None = None) -> None:
        super().__init__(initial or {})
        self.accessed = False
        self.modified = False

    def __getitem__(self, key: str) -> Any:
        self.accessed = True
        return super().__getitem__(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.accessed = True
        self.modified = True
        super().__setitem__(key, value)

    def __delitem__(self, key: str) -> None:
        self.accessed = True
        self.modified = True
        super().__delitem__(key)

    def __contains__(self, key: object) -> bool:
        self.accessed = True
        return super().__contains__(key)

    def __iter__(self):
        self.accessed = True
        return super().__iter__()

    def __len__(self) -> int:
        self.accessed = True
        return super().__len__()

    def __bool__(self) -> bool:
        self.accessed = True
        return super().__len__() > 0

    def clear(self) -> None:
        self.accessed = True
        self.modified = True
        super().clear()

    def get(self, key: str, default: Any = None) -> Any:
        self.accessed = True
        return super().get(key, default)

    def pop(self, key: str, default: Any = ...):
        self.accessed = True
        self.modified = True
        if default is ...:
            return super().pop(key)
        return super().pop(key, default)

    def popitem(self) -> tuple[str, Any]:
        self.accessed = True
        self.modified = True
        return super().popitem()

    def setdefault(self, key: str, default: Any = None) -> Any:
        self.accessed = True
        if key not in self:
            self.modified = True
        return super().setdefault(key, default)

    def update(
        self,
        other: Mapping[str, Any] | Iterable[tuple[str, Any]] = (),
        /,
        **kwargs: Any,
    ) -> None:
        self.accessed = True
        if other or kwargs:
            self.modified = True
        super().update(other, **kwargs)


def request_is_https(scope: Scope) -> bool:
    """Return True when upstream middleware has resolved the request as HTTPS."""
    return scope.get("scheme") == "https"


class RequestAwareSessionMiddleware(SessionMiddleware):
    """Apply the Secure cookie flag only when the effective request is HTTPS."""

    def __init__(
        self,
        app: ASGIApp,
        secret_key: str,
        session_cookie: str = "session",
        max_age: int | None = 14 * 24 * 60 * 60,
        path: str = "/",
        same_site: Literal["lax", "strict", "none"] = "lax",
        https_only: bool = False,
        domain: str | None = None,
    ) -> None:
        self.app = app
        self.signer = itsdangerous.TimestampSigner(str(secret_key))
        self.session_cookie = session_cookie
        self.max_age = max_age
        self.path = path
        normalized_same_site = same_site.lower()
        if normalized_same_site not in {"lax", "strict", "none"}:
            raise ValueError("Invalid same_site value")
        self.same_site = normalized_same_site
        self.https_only = https_only
        self.domain = domain

        if self.same_site == "none" and not self.https_only:
            raise ValueError("same_site='none' requires https_only=True")

    def _request_is_https(self, scope: Scope) -> bool:
        return request_is_https(scope)

    def _security_flags(self, scope: Scope) -> str:
        flags = f"httponly; samesite={self.same_site}"
        if self.https_only and self._request_is_https(scope):
            flags += "; secure"
        if self.domain is not None:
            flags += f"; domain={self.domain}"
        return flags

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):  # pragma: no cover
            await self.app(scope, receive, send)
            return

        connection = HTTPConnection(scope)
        initial_session_was_empty = True

        if self.session_cookie in connection.cookies:
            data = connection.cookies[self.session_cookie].encode("utf-8")
            try:
                data = self.signer.unsign(data, max_age=self.max_age)
                scope["session"] = _TrackedSession(json.loads(b64decode(data)))
                initial_session_was_empty = False
            except itsdangerous.BadSignature:
                scope["session"] = _TrackedSession()
        else:
            scope["session"] = _TrackedSession()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                session: _TrackedSession = scope["session"]
                headers = MutableHeaders(scope=message)
                if session.accessed:
                    headers.add_vary_header("Cookie")
                security_flags = self._security_flags(scope)
                if session.modified and session:
                    data = b64encode(json.dumps(session).encode("utf-8"))
                    data = self.signer.sign(data)
                    header_value = "{session_cookie}={data}; path={path}; {max_age}{security_flags}".format(
                        session_cookie=self.session_cookie,
                        data=data.decode("utf-8"),
                        path=self.path,
                        max_age=f"Max-Age={self.max_age}; " if self.max_age else "",
                        security_flags=security_flags,
                    )
                    headers.append("Set-Cookie", header_value)
                elif session.modified and not initial_session_was_empty:
                    header_value = "{session_cookie}={data}; path={path}; {expires}{security_flags}".format(
                        session_cookie=self.session_cookie,
                        data="null",
                        path=self.path,
                        expires="expires=Thu, 01 Jan 1970 00:00:00 GMT; ",
                        security_flags=security_flags,
                    )
                    headers.append("Set-Cookie", header_value)
            await send(message)

        await self.app(scope, receive, send_wrapper)
