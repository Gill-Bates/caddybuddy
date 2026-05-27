#!/usr/bin/env python3
#
# tests/test_caddy_api_sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import UTC, datetime
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, Response

_ENV_OVERRIDES = {
    "CADDYBUDDY_SECRET_KEY": "unit-test-secret-key",
    "CADDYBUDDY_ADMIN_PASSWORD": "unit-test-password",
}
_ORIGINAL_ENV = {key: os.environ.get(key) for key in _ENV_OVERRIDES}

for key, value in _ENV_OVERRIDES.items():
    os.environ[key] = value

from app.repositories.sites import DuplicateSiteError
from app.routers.caddy_api import create_site, update_site
from app.schemas.caddy import SiteCreateRequest, SiteUpdateRequest


def tearDownModule() -> None:
    for key, original_value in _ORIGINAL_ENV.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value


class CaddyApiSiteMutationTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_site_returns_409_on_duplicate_race(self) -> None:
        create_site_fn = getattr(create_site, "__wrapped__", create_site)
        payload = SiteCreateRequest(
            domain="example.com",
            caddy_directives="reverse_proxy backend.example.test:443",
            enabled=True,
        )
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

        with (
            patch("app.routers.caddy_api.site_repository.domain_exists", new=AsyncMock(return_value=False)),
            patch(
                "app.routers.caddy_api.site_repository.create",
                new=AsyncMock(side_effect=DuplicateSiteError("Site domain already exists.")),
            ),
        ):
            with self.assertRaises(HTTPException) as context:
                await create_site_fn(
                    payload,
                    request=SimpleNamespace(),
                    response=Response(),
                    session=session,
                    _current_user=SimpleNamespace(is_admin=True),
                )

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Site domain already exists.")
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()

    async def test_create_site_sync_failure_returns_accepted_and_publishes_event(self) -> None:
        create_site_fn = getattr(create_site, "__wrapped__", create_site)
        payload = SiteCreateRequest(
            domain="example.com",
            caddy_directives="reverse_proxy backend.example.test:443",
            enabled=True,
        )
        response = Response()
        site = SimpleNamespace(
            id=7,
            domain="example.com",
            upstream_url="http://backend.example.test:443",
            caddy_directives="reverse_proxy backend.example.test:443",
            enabled=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        sync_result = SimpleNamespace(
            status="sync_failed",
            synced=False,
            config_sha256="abc123",
            error="Caddy Admin API unavailable.",
            error_code="caddy_admin_unavailable",
        )

        with (
            patch("app.routers.caddy_api.site_repository.domain_exists", new=AsyncMock(return_value=False)),
            patch("app.routers.caddy_api.site_repository.create", new=AsyncMock(return_value=site)),
            patch("app.routers.caddy_api.sync_caddy_configuration", new=AsyncMock(return_value=sync_result)),
            patch("app.routers.caddy_api.publish_resource_event", new=AsyncMock()) as publish_event,
        ):
            result = await create_site_fn(
                payload,
                request=SimpleNamespace(),
                response=response,
                session=session,
                _current_user=SimpleNamespace(is_admin=True),
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(result.status, "created")
        self.assertEqual(result.sync_status, "sync_failed")
        self.assertFalse(result.synced)
        self.assertEqual(result.site.id, 7)
        self.assertEqual(session.commit.await_count, 2)
        publish_event.assert_awaited_once_with("site", "created", "7")

    async def test_update_site_returns_409_on_duplicate_race(self) -> None:
        update_site_fn = getattr(update_site, "__wrapped__", update_site)
        payload = SiteUpdateRequest(
            domain="example.com",
            caddy_directives="reverse_proxy backend.example.test:443",
            enabled=True,
        )
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        existing_site = SimpleNamespace(id=1, domain="old.example.com")

        with (
            patch("app.routers.caddy_api.site_repository.get_by_id", new=AsyncMock(return_value=existing_site)),
            patch("app.routers.caddy_api.site_repository.domain_exists", new=AsyncMock(return_value=False)),
            patch(
                "app.routers.caddy_api.site_repository.update",
                new=AsyncMock(side_effect=DuplicateSiteError("Site domain already exists.")),
            ),
        ):
            with self.assertRaises(HTTPException) as context:
                await update_site_fn(
                    1,
                    payload,
                    request=SimpleNamespace(),
                    response=Response(),
                    session=session,
                    _current_user=SimpleNamespace(is_admin=True),
                )

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Site domain already exists.")
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()

    async def test_update_site_sync_failure_returns_accepted_and_publishes_event(self) -> None:
        update_site_fn = getattr(update_site, "__wrapped__", update_site)
        payload = SiteUpdateRequest(
            domain="example.com",
            caddy_directives="reverse_proxy backend.example.test:443",
            enabled=True,
        )
        response = Response()
        updated_site = SimpleNamespace(
            id=1,
            domain="example.com",
            upstream_url="http://backend.example.test:443",
            caddy_directives="reverse_proxy backend.example.test:443",
            enabled=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        sync_result = SimpleNamespace(
            status="admin_api_unavailable",
            synced=False,
            config_sha256="def456",
            error="Caddy Admin API unavailable.",
            error_code="caddy_admin_unavailable",
        )

        with (
            patch("app.routers.caddy_api.site_repository.get_by_id", new=AsyncMock(return_value=updated_site)),
            patch("app.routers.caddy_api.site_repository.domain_exists", new=AsyncMock(return_value=False)),
            patch("app.routers.caddy_api.site_repository.update", new=AsyncMock(return_value=updated_site)),
            patch("app.routers.caddy_api.sync_caddy_configuration", new=AsyncMock(return_value=sync_result)),
            patch("app.routers.caddy_api.publish_resource_event", new=AsyncMock()) as publish_event,
        ):
            result = await update_site_fn(
                1,
                payload,
                request=SimpleNamespace(),
                response=response,
                session=session,
                _current_user=SimpleNamespace(is_admin=True),
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(result.status, "updated")
        self.assertEqual(result.sync_status, "admin_api_unavailable")
        self.assertFalse(result.synced)
        self.assertEqual(session.commit.await_count, 2)
        publish_event.assert_awaited_once_with("site", "updated", "1")


if __name__ == "__main__":
    unittest.main()