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
    "CB_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key-for-testing",
    "CB_ADMIN_PASSWORD": "UnitTestPassword-123A",
    "CADDYBUDDY_ADMIN_PASSWORD": "UnitTestPassword-123A",
}
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}

for key, value in _ENV_OVERRIDES.items():
    os.environ[key] = value

get_settings.cache_clear()

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import app.main as main_module


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
    get_settings.cache_clear()


def _request(
    path: str,
    *,
    referer: str | None = None,
    accept: str | None = None,
    requested_with: str | None = None,
) -> Request:
    headers = []
    if referer is not None:
        headers.append((b"referer", referer.encode("utf-8")))
    if accept is not None:
        headers.append((b"accept", accept.encode("utf-8")))
    if requested_with is not None:
        headers.append((b"x-requested-with", requested_with.encode("utf-8")))
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

    def test_safe_rate_limit_redirect_path_rejects_encoded_control_characters(self) -> None:
        request = _request("/sites", referer="http://testserver/%0d/sites?token=secret")

        self.assertEqual(main_module._safe_rate_limit_redirect_path(request), "/")

    async def test_handle_rate_limit_exceeded_awaits_async_api_handler(self) -> None:
        request = _request("/api/sites")

        async def _async_handler(_request, _exc):
            return "handled"

        with patch.object(main_module, "_rate_limit_exceeded_handler", new=_async_handler):
            response = await main_module._handle_rate_limit_exceeded(request, SimpleNamespace())

        self.assertEqual(response, "handled")

    async def test_handle_rate_limit_exceeded_treats_json_accept_as_api(self) -> None:
        request = _request("/login", accept="application/json")

        async def _async_handler(_request, _exc):
            return "handled"

        with patch.object(main_module, "_rate_limit_exceeded_handler", new=_async_handler):
            response = await main_module._handle_rate_limit_exceeded(request, SimpleNamespace())

        self.assertEqual(response, "handled")

    async def test_handle_rate_limit_exceeded_degrades_gracefully_without_session(self) -> None:
        request = _request("/login")

        with patch.object(main_module, "push_flash", side_effect=AssertionError("no session")):
            response = await main_module._handle_rate_limit_exceeded(request, SimpleNamespace())

        self.assertEqual(response.status_code, 429)
        self.assertIn("Too many attempts", response.body.decode("utf-8"))

    async def test_handle_rate_limit_exceeded_renders_login_page_with_429_when_session_exists(self) -> None:
        request = _request("/login")
        request.scope["session"] = {}

        with (
            patch.object(main_module, "push_flash") as push_flash,
            patch.object(main_module, "render_template", return_value="rendered") as render_template,
        ):
            response = await main_module._handle_rate_limit_exceeded(request, SimpleNamespace())

        self.assertEqual(response, "rendered")
        push_flash.assert_called_once_with(request, "danger", "Too many attempts. Please wait a minute and try again.")
        render_template.assert_called_once()
        self.assertEqual(render_template.call_args.kwargs["status_code"], 429)
        self.assertEqual(render_template.call_args.kwargs["context"]["setup_mode"], False)

    def test_create_app_registers_csrf_session_and_security_middlewares(self) -> None:
        app = main_module.create_app()

        middleware_classes = [middleware.cls.__name__ for middleware in app.user_middleware]
        self.assertEqual(
            middleware_classes[:4],
            [
                "SecurityHeadersMiddleware",
                "RequestAwareSessionMiddleware",
                "StartupReconcileMiddleware",
                "CSRFMiddleware",
            ],
        )

    def test_startup_gate_blocks_mutating_requests_until_ready(self) -> None:
        app = FastAPI()
        app.state.caddy_reconcile_ready = False

        @app.post("/api/sites")
        async def api_sites() -> dict[str, bool]:
            return {"ok": True}

        @app.post("/sites")
        async def ui_sites() -> dict[str, bool]:
            return {"ok": True}

        app.add_middleware(main_module.StartupReconcileMiddleware)

        with TestClient(app) as client:
            api_response = client.post("/api/sites", headers={"Accept": "application/json"})
            ui_response = client.post("/sites")

        self.assertEqual(api_response.status_code, 503)
        self.assertEqual(
            api_response.json(),
            {"detail": "Caddy startup synchronization is still running. Please retry in a moment."},
        )
        self.assertEqual(ui_response.status_code, 503)
        self.assertIn("Caddy startup synchronization is still running.", ui_response.text)

    def test_startup_gate_allows_settings_caddy_when_reconcile_is_not_ready(self) -> None:
        app = FastAPI()
        app.state.caddy_reconcile_ready = False

        @app.post("/settings/caddy")
        async def settings_caddy() -> dict[str, bool]:
            return {"ok": True}

        app.add_middleware(main_module.StartupReconcileMiddleware)

        with TestClient(app) as client:
            response = client.post("/settings/caddy")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_request_aware_session_middleware_omits_secure_cookie_on_http(self) -> None:
        app = FastAPI()

        @app.get("/session")
        async def session_page(request: Request):
            request.session["visited"] = True
            return {"ok": True}

        app.add_middleware(
            main_module.RequestAwareSessionMiddleware,
            secret_key="unit-test-secret-key-for-testing",
            https_only=True,
        )

        with TestClient(app, base_url="http://testserver") as client:
            response = client.get("/session")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("secure", response.headers.get("set-cookie", "").lower())

    def test_request_aware_session_middleware_keeps_secure_cookie_on_https(self) -> None:
        app = FastAPI()

        @app.get("/session")
        async def session_page(request: Request):
            request.session["visited"] = True
            return {"ok": True}

        app.add_middleware(
            main_module.RequestAwareSessionMiddleware,
            secret_key="unit-test-secret-key-for-testing",
            https_only=True,
        )

        with TestClient(app, base_url="https://testserver") as client:
            response = client.get("/session")

        self.assertEqual(response.status_code, 200)
        self.assertIn("secure", response.headers.get("set-cookie", "").lower())

    def test_request_aware_session_middleware_ignores_forwarded_proto_header(self) -> None:
        app = FastAPI()

        @app.get("/session")
        async def session_page(request: Request):
            request.session["visited"] = True
            return {"ok": True}

        app.add_middleware(
            main_module.RequestAwareSessionMiddleware,
            secret_key="unit-test-secret-key-for-testing",
            https_only=True,
        )

        with TestClient(app, base_url="http://testserver") as client:
            response = client.get("/session", headers={"X-Forwarded-Proto": "https"})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("secure", response.headers.get("set-cookie", "").lower())

    def test_request_aware_session_middleware_rejects_samesite_none_without_https_only(self) -> None:
        app = FastAPI()

        app.add_middleware(
            main_module.RequestAwareSessionMiddleware,
            secret_key="unit-test-secret-key-for-testing",
            same_site="none",
            https_only=False,
        )

        with self.assertRaisesRegex(ValueError, "same_site='none' requires https_only=True"):
            app.build_middleware_stack()

    def test_request_aware_session_middleware_normalizes_samesite_case(self) -> None:
        app = FastAPI()

        @app.get("/session")
        async def session_page(request: Request):
            request.session["visited"] = True
            return {"ok": True}

        app.add_middleware(
            main_module.RequestAwareSessionMiddleware,
            secret_key="unit-test-secret-key-for-testing",
            same_site="NONE",
            https_only=True,
        )

        with TestClient(app, base_url="https://testserver") as client:
            response = client.get("/session")

        self.assertEqual(response.status_code, 200)
        self.assertIn("samesite=none", response.headers.get("set-cookie", "").lower())

    async def test_reconcile_caddy_on_startup_logs_unexpected_exceptions(self) -> None:
        application = FastAPI()
        settings = SimpleNamespace()
        session_factory = SimpleNamespace()

        with (
            patch.object(
                main_module,
                "_run_caddy_startup_reconcile",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            self.assertLogs(main_module.logger, level="ERROR") as logs,
        ):
            await main_module._reconcile_caddy_on_startup(application, session_factory, settings)

        self.assertTrue(any("Unexpected Caddy startup reconcile failure." in line for line in logs.output))

    async def test_run_caddy_startup_reconcile_retries_transient_sync_failure_until_success(self) -> None:
        settings = SimpleNamespace(auto_onboard=True, caddy_startup_reconcile_timeout_seconds=10.0)

        session_one = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        session_two = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        session_three = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        session_four = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        session_five = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        session_factory = _SessionFactory(session_one, session_two, session_three, session_four, session_five)

        managed_status = SimpleNamespace(
            onboarding_required=False,
            admin_api_reachable=True,
            managed=True,
            caddyfile_path="/app/Caddyfile",
            error=None,
        )
        retryable_failure = SimpleNamespace(
            status="sync_failed",
            synced=False,
            config_sha256="abc123",
            error="Caddy Admin API unavailable.",
            error_code="caddy_admin_unavailable",
        )
        success_result = SimpleNamespace(
            status="synced",
            synced=True,
            config_sha256="def456",
            error=None,
            error_code=None,
        )
        final_status = SimpleNamespace(
            onboarding_required=False,
            admin_api_reachable=True,
            managed=True,
            caddyfile_path="/app/Caddyfile",
            error=None,
        )

        with (
            patch.object(
                main_module,
                "get_caddy_runtime_status",
                new=AsyncMock(side_effect=[managed_status, managed_status, final_status]),
            ) as get_status,
            patch.object(
                main_module,
                "sync_caddy_configuration",
                new=AsyncMock(side_effect=[retryable_failure, success_result]),
            ) as sync_config,
            patch.object(main_module.asyncio, "sleep", new=AsyncMock()) as sleep_mock,
        ):
            application = FastAPI()
            await main_module._run_caddy_startup_reconcile(application, session_factory, settings)

        self.assertEqual(get_status.await_count, 3)
        self.assertEqual(sync_config.await_count, 2)
        sleep_mock.assert_awaited_once()
        session_two.rollback.assert_awaited_once()
        session_two.commit.assert_not_awaited()
        session_four.commit.assert_awaited_once()
        session_four.rollback.assert_not_awaited()
        self.assertIs(application.state.caddy_status, final_status)

    async def test_run_caddy_startup_reconcile_refreshes_status_before_timeout_return(self) -> None:
        settings = SimpleNamespace(auto_onboard=True, caddy_startup_reconcile_timeout_seconds=0.0)

        session_one = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        session_two = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        session_factory = _SessionFactory(session_one, session_two)

        unreachable_status = SimpleNamespace(
            onboarding_required=False,
            admin_api_reachable=False,
            managed=False,
            caddyfile_path="/app/Caddyfile",
            error="Admin API not reachable",
        )
        refreshed_status = SimpleNamespace(
            onboarding_required=False,
            admin_api_reachable=False,
            managed=False,
            caddyfile_path="/app/Caddyfile",
            error="still unreachable",
        )

        with (
            patch.object(
                main_module,
                "get_caddy_runtime_status",
                new=AsyncMock(side_effect=[unreachable_status, refreshed_status]),
            ) as get_status,
            patch.object(main_module.time, "monotonic", side_effect=[100.0, 100.0]),
            self.assertLogs(main_module.logger, level="ERROR"),
        ):
            application = FastAPI()
            await main_module._run_caddy_startup_reconcile(application, session_factory, settings)

        self.assertEqual(get_status.await_count, 2)
        self.assertIs(application.state.caddy_status, refreshed_status)


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
            auto_onboard=True,
            caddy_startup_reconcile_timeout_seconds=5.0,
        )

        session_one = SimpleNamespace(begin=lambda: _AsyncContextManager(), commit=AsyncMock(), rollback=AsyncMock())
        session_two = SimpleNamespace(begin=lambda: _AsyncContextManager(), commit=AsyncMock(), rollback=AsyncMock())
        session_three = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        session_four = SimpleNamespace(begin=lambda: _AsyncContextManager(), commit=AsyncMock(), rollback=AsyncMock())
        session_factory = _SessionFactory(
            session_one, session_two, session_three, session_four
        )

        dispose_engine = AsyncMock()

        onboarding_status = SimpleNamespace(
            onboarding_required=True,
            admin_api_reachable=True,
            managed=False,
            caddyfile_path="/app/Caddyfile",
            error=None,
        )
        managed_status = SimpleNamespace(
            onboarding_required=False,
            admin_api_reachable=True,
            managed=True,
            caddyfile_path="/app/Caddyfile",
            error=None,
        )

        with (
            patch.object(main_module, "get_settings", return_value=settings),
            patch.object(main_module, "init_database", new=AsyncMock()),
            patch.object(main_module, "dispose_engine", new=dispose_engine),
            patch.object(main_module, "get_session_factory", return_value=session_factory),
            patch.object(main_module, "get_rate_limit_enabled", new=AsyncMock(return_value=True)),
            patch.object(main_module, "update_rate_limit_enabled"),
            patch.object(
                main_module,
                "get_caddy_runtime_status",
                new=AsyncMock(side_effect=[onboarding_status, onboarding_status, managed_status]),
            ) as get_status,
            patch.object(main_module, "onboard_caddy", new=AsyncMock(return_value=SimpleNamespace(status="onboarded", error=None))) as onboard,
            patch.object(main_module.ssllabs_service, "startup", new=AsyncMock()),
            patch.object(main_module.ssllabs_service, "shutdown", new=AsyncMock()),
            patch.object(main_module.event_bus, "shutdown", new=AsyncMock()),
            patch.object(main_module.caddy_service, "aclose", new=AsyncMock()),
        ):
            application = FastAPI()
            async with main_module.lifespan(application):
                # Reconciliation runs as a background task; await it so the
                # onboarding path completes before assertions.
                self.assertEqual(application.state.caddy_reconcile_task.get_name(), "caddy-startup-reconcile")
                await application.state.caddy_reconcile_task

        self.assertIsNone(application.state.caddy_reconcile_task)

        # The inline startup status check and the background reconcile each use
        # their own session rather than sharing one.
        onboard.assert_awaited_once()
        self.assertEqual(get_status.await_count, 3)
        self.assertEqual(get_status.await_args_list[0].args[0], session_one)
        self.assertEqual(get_status.await_args_list[1].args[0], session_two)
        self.assertEqual(get_status.await_args_list[2].args[0], session_four)
        self.assertEqual(onboard.await_args_list[0].args[0], session_three)
        session_three.commit.assert_awaited_once()
        session_three.rollback.assert_not_awaited()
        dispose_engine.assert_awaited_once()

    async def test_run_caddy_startup_reconcile_commits_successful_sync(self) -> None:
        settings = SimpleNamespace(
            auto_onboard=True,
            caddy_startup_reconcile_timeout_seconds=5.0,
        )

        session_one = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        session_two = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        session_three = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        session_factory = _SessionFactory(session_one, session_two, session_three)

        managed_status = SimpleNamespace(
            onboarding_required=False,
            admin_api_reachable=True,
            managed=True,
            caddyfile_path="/app/Caddyfile",
            error=None,
        )

        with (
            patch.object(
                main_module,
                "get_caddy_runtime_status",
                new=AsyncMock(side_effect=[managed_status, managed_status]),
            ) as get_status,
            patch.object(
                main_module,
                "sync_caddy_configuration",
                new=AsyncMock(return_value=SimpleNamespace(status="synced", synced=True, error=None)),
            ) as sync_config,
        ):
            application = FastAPI()
            await main_module._run_caddy_startup_reconcile(application, session_factory, settings)

        self.assertEqual(get_status.await_count, 2)
        sync_config.assert_awaited_once_with(session_two, force=True)
        session_two.commit.assert_awaited_once()
        session_two.rollback.assert_not_awaited()


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
