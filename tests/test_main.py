#!/usr/bin/env python3
#
# tests/test_main.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config.settings import get_settings


_ENV_OVERRIDES = {
    "CB_SECRET_KEY": "unit-test-secret-key",
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key",
    "CB_ADMIN_PASSWORD": "UnitTestPassword-123A",
    "CADDYBUDDY_ADMIN_PASSWORD": "UnitTestPassword-123A",
}
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}

for key, value in _ENV_OVERRIDES.items():
    os.environ[key] = value

get_settings.cache_clear()

from fastapi import FastAPI, Request

import app.main as main_module


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


def _request(path: str, *, referer: str | None = None) -> Request:
    headers = []
    if referer is not None:
        headers.append((b"referer", referer.encode("utf-8")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "headers": headers,
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
            "app": FastAPI(),
        }
    )


class MainModuleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        for key, value in _ENV_OVERRIDES.items():
            os.environ[key] = value
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_safe_rate_limit_redirect_path_strips_query(self) -> None:
        request = _request("/sites", referer="http://testserver/sites?token=secret")

        self.assertEqual(main_module._safe_rate_limit_redirect_path(request), "/sites")

    async def test_handle_rate_limit_exceeded_awaits_async_api_handler(self) -> None:
        request = _request("/api/sites")

        async def _async_handler(_request, _exc):
            return "handled"

        with patch.object(main_module, "_rate_limit_exceeded_handler", new=_async_handler):
            response = await main_module._handle_rate_limit_exceeded(request, SimpleNamespace())

        self.assertEqual(response, "handled")

    async def test_handle_rate_limit_exceeded_degrades_gracefully_without_session(self) -> None:
        request = _request("/login")

        with patch.object(main_module, "push_flash", side_effect=AssertionError("no session")):
            response = await main_module._handle_rate_limit_exceeded(request, SimpleNamespace())

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_create_app_registers_csrf_session_and_security_middlewares(self) -> None:
        app = main_module.create_app()

        middleware_classes = [middleware.cls.__name__ for middleware in app.user_middleware]
        self.assertEqual(
            middleware_classes[:3],
            ["SecurityHeadersMiddleware", "SessionMiddleware", "CSRFMiddleware"],
        )


class LifespanTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        for key, value in _ENV_OVERRIDES.items():
            os.environ[key] = value
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()

    async def test_lifespan_uses_separate_session_for_auto_onboarding(self) -> None:
        settings = SimpleNamespace(
            data_dir=SimpleNamespace(mkdir=lambda parents, exist_ok: None),
            default_admin_username="admin",
            default_admin_password=SimpleNamespace(get_secret_value=lambda: "UnitTestPassword-123A"),
            default_admin_email="admin@example.com",
            auto_onboard=True,
        )

        session_one = SimpleNamespace(
            begin=lambda: _AsyncContextManager(),
        )
        session_two = SimpleNamespace(
            begin=lambda: _AsyncContextManager(),
        )
        session_three = SimpleNamespace(
            begin=lambda: _AsyncContextManager(),
        )
        session_four = SimpleNamespace()
        session_factory = _SessionFactory(session_one, session_two, session_three, session_four)

        dispose_engine = AsyncMock()

        with (
            patch.object(main_module, "get_settings", return_value=settings),
            patch.object(main_module, "init_database", new=AsyncMock()),
            patch.object(main_module, "dispose_engine", new=dispose_engine),
            patch.object(main_module, "get_session_factory", return_value=session_factory),
            patch.object(main_module.auth_service, "ensure_default_admin", new=AsyncMock(return_value=None)) as ensure_admin,
            patch.object(main_module, "get_rate_limit_enabled", new=AsyncMock(return_value=True)),
            patch.object(main_module, "update_rate_limit_enabled"),
            patch.object(
                main_module,
                "get_caddy_runtime_status",
                new=AsyncMock(side_effect=[
                    SimpleNamespace(onboarding_required=True, caddyfile_path="/app/Caddyfile", error=None),
                    SimpleNamespace(onboarding_required=False, caddyfile_path="/app/Caddyfile", error=None),
                ]),
            ) as get_status,
            patch.object(main_module, "onboard_caddy", new=AsyncMock(return_value=SimpleNamespace(status="onboarded", error=None))),
            patch.object(main_module.ssllabs_service, "startup", new=AsyncMock()),
            patch.object(main_module.ssllabs_service, "shutdown", new=AsyncMock()),
            patch.object(main_module.event_bus, "shutdown", new=AsyncMock()),
            patch.object(main_module.caddy_service, "aclose", new=AsyncMock()),
        ):
            application = FastAPI()
            async with main_module.lifespan(application):
                pass

        ensure_admin.assert_awaited_once_with(
            session_one,
            username="admin",
            password="UnitTestPassword-123A",
            email="admin@example.com",
        )
        self.assertEqual(get_status.await_args_list[0].args[0], session_two)
        self.assertEqual(get_status.await_args_list[1].args[0], session_four)
        dispose_engine.assert_awaited_once()


class _AsyncContextManager:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, *sessions):
        self._sessions = list(sessions)

    def __call__(self):
        return _SessionContext(self._sessions.pop(0))


if __name__ == "__main__":
    unittest.main()