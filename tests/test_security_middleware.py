#!/usr/bin/env python3
#
# tests/test_security_middleware.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.config.settings import get_settings


_ENV_OVERRIDES = {
    "CB_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CB_ADMIN_PASSWORD": "UnitTestPassword-123A",
    "CADDYBUDDY_ADMIN_PASSWORD": "UnitTestPassword-123A",
}

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware


class _SecurityTestEnvMixin:
    def setUp(self) -> None:
        self._env_patch = patch.dict(os.environ, _ENV_OVERRIDES, clear=False)
        self._env_patch.start()
        get_settings.cache_clear()
        from app.dependencies.web import ensure_csrf_token
        from app.middleware import csrf as csrf_module

        self._ensure_csrf_token = ensure_csrf_token
        self._csrf_module = csrf_module
        self._csrf_module._auth_cookie_names.cache_clear()

    def tearDown(self) -> None:
        self._env_patch.stop()
        get_settings.cache_clear()
        self._csrf_module._auth_cookie_names.cache_clear()


class SecurityHeadersMiddlewareTests(_SecurityTestEnvMixin, unittest.TestCase):
    def test_security_headers_are_added_to_http_responses(self) -> None:
        app = FastAPI()
        app.add_middleware(self._csrf_module.SecurityHeadersMiddleware)

        @app.get("/")
        async def index() -> dict[str, str]:
            return {"status": "ok"}

        with TestClient(app) as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("content-security-policy", response.headers)
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["referrer-policy"], "strict-origin-when-cross-origin")
        self.assertEqual(
            response.headers["permissions-policy"],
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        self.assertEqual(response.headers["cross-origin-opener-policy"], "same-origin")
        self.assertNotIn("strict-transport-security", response.headers)

    def test_hsts_is_added_for_https_requests(self) -> None:
        app = FastAPI()
        app.add_middleware(self._csrf_module.SecurityHeadersMiddleware)

        @app.get("/")
        async def index() -> dict[str, str]:
            return {"status": "ok"}

        with TestClient(app, base_url="https://example.test") as client:
            response = client.get("/")

        self.assertEqual(
            response.headers["strict-transport-security"],
            "max-age=63072000; includeSubDomains",
        )


class CSRFMiddlewareTests(_SecurityTestEnvMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()

    def tearDown(self) -> None:
        super().tearDown()

    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(self._csrf_module.CSRFMiddleware)
        app.add_middleware(SessionMiddleware, secret_key="unit-test-secret-key-for-testing")

        @app.get("/login")
        async def login_page(request: Request) -> PlainTextResponse:
            return PlainTextResponse(self._ensure_csrf_token(request))

        @app.post("/api/protected")
        async def protected() -> PlainTextResponse:
            return PlainTextResponse("ok")

        return app

    def _settings_patch(self):
        return patch(
            "app.middleware.csrf.get_settings",
            return_value=SimpleNamespace(session_cookie_name="caddybuddy_session"),
        )

    def test_api_request_uses_configured_session_cookie_name_for_csrf_enforcement(self) -> None:
        app = self._build_app()

        with self._settings_patch():
            with TestClient(app) as client:
                csrf_token = client.get("/login").text
                client.cookies.set("caddybuddy_session", client.cookies.get("session") or "session-cookie")
                response = client.post("/api/protected")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "CSRF token missing or invalid"})

    def test_origin_check_rejects_same_host_different_port(self) -> None:
        app = self._build_app()

        with self._settings_patch():
            with TestClient(app, base_url="https://example.test") as client:
                csrf_token = client.get("/login").text
                client.cookies.set("caddybuddy_session", client.cookies.get("session") or "session-cookie")
                response = client.post(
                    "/api/protected",
                    headers={
                        "Origin": "https://example.test:444",
                        "X-CSRF-Token": csrf_token,
                    },
                )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Cross-origin request blocked"})

    def test_bearer_header_does_not_bypass_cookie_authenticated_api_request(self) -> None:
        app = self._build_app()

        with self._settings_patch():
            with TestClient(app) as client:
                client.get("/login")
                client.cookies.set("caddybuddy_session", client.cookies.get("session") or "session-cookie")
                response = client.post(
                    "/api/protected",
                    headers={"Authorization": "bearer token-value"},
                )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "CSRF token missing or invalid"})

    def test_stateless_bearer_api_request_skips_csrf(self) -> None:
        app = self._build_app()

        with self._settings_patch():
            with TestClient(app) as client:
                response = client.post(
                    "/api/protected",
                    headers={"Authorization": "Bearer token-value"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "ok")

    def test_multipart_request_requires_header_without_parsing_form_body(self) -> None:
        app = self._build_app()

        with self._settings_patch():
            with TestClient(app) as client:
                csrf_token = client.get("/login").text
                client.cookies.set("caddybuddy_session", client.cookies.get("session") or "session-cookie")
                with patch("starlette.requests.Request.form", side_effect=AssertionError("form() should not be called")):
                    response = client.post(
                        "/api/protected",
                        files={"csrf_token": (None, csrf_token)},
                    )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "CSRF token missing or invalid"})

    def test_cookie_authenticated_api_request_accepts_valid_csrf_header(self) -> None:
        app = self._build_app()

        with self._settings_patch():
            with TestClient(app) as client:
                csrf_token = client.get("/login").text
                client.cookies.set("caddybuddy_session", client.cookies.get("session") or "session-cookie")
                response = client.post(
                    "/api/protected",
                    headers={"X-CSRF-Token": csrf_token},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "ok")

    def test_cookie_authenticated_api_request_rejects_invalid_csrf_header(self) -> None:
        app = self._build_app()

        with self._settings_patch():
            with TestClient(app) as client:
                client.get("/login")
                client.cookies.set("caddybuddy_session", client.cookies.get("session") or "session-cookie")
                response = client.post(
                    "/api/protected",
                    headers={"X-CSRF-Token": "invalid"},
                )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "CSRF token missing or invalid"})

    def test_safe_requests_prime_session_csrf_token(self) -> None:
        app = self._build_app()

        with TestClient(app) as client:
            response = client.get("/login")

        self.assertRegex(response.text, r"^[A-Za-z0-9_-]+\.[0-9a-f]{64}$")
        self.assertTrue(client.cookies.get("session"))

    def test_forwarded_proto_header_alone_does_not_enable_hsts(self) -> None:
        app = FastAPI()
        app.add_middleware(self._csrf_module.SecurityHeadersMiddleware)

        @app.get("/")
        async def index() -> dict[str, str]:
            return {"status": "ok"}

        with TestClient(app) as client:
            response = client.get("/", headers={"X-Forwarded-Proto": "https"})

        self.assertNotIn("strict-transport-security", response.headers)


if __name__ == "__main__":
    unittest.main()