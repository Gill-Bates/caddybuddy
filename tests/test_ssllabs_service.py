#!/usr/bin/env python3
#
# tests/test_ssllabs_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.entities import Site, SslLabsScan, SslLabsTarget
from app.repositories.ssllabs import ACTIVE_SCAN_STALE_AFTER, site_uses_https, ssllabs_repository
from app.services.ssllabs import (
    SslLabsClient,
    SslLabsClientSettings,
    SslLabsEmailNotRegisteredError,
    SslLabsRetryableError,
    SslLabsService,
)
from app.utils.ssllabs import next_schedule_time, schedule_interval, status_badge_class, validate_ssllabs_host


def _transport(handler):
    return httpx.MockTransport(handler)


class SslLabsClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_adds_registered_email_header(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["email"], "security@example.com")
            self.assertEqual(request.url.path, "/api/v4/analyze")
            self.assertEqual(request.url.params["host"], "example.com")
            self.assertEqual(request.url.params["fromCache"], "on")
            self.assertEqual(request.url.params["maxAge"], "24")
            return httpx.Response(200, json={"status": "READY", "host": "example.com"})

        client = SslLabsClient(
            SslLabsClientSettings(
                api_base_url="https://api.ssllabs.com/api/v4",
                email="security@example.com",
            ),
            transport=_transport(handler),
        )

        try:
            payload = await client.analyze(host="example.com", from_cache=True, max_age_hours=24)
        finally:
            await client.aclose()

        self.assertEqual(payload["status"], "READY")

    async def test_client_maps_429_to_retryable_error(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "901"}, text=json.dumps({"message": "slow down"}))

        client = SslLabsClient(
            SslLabsClientSettings(
                api_base_url="https://api.ssllabs.com/api/v4",
                email="security@example.com",
            ),
            transport=_transport(handler),
        )

        try:
            with self.assertRaises(SslLabsRetryableError) as exc:
                await client.analyze(host="example.com", start_new=True)
        finally:
            await client.aclose()

        self.assertEqual(exc.exception.status_code, 429)
        self.assertGreaterEqual(exc.exception.retry_after_seconds, 901)

    async def test_client_caps_retry_after_to_one_hour(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "999999"}, text=json.dumps({"message": "slow down"}))

        client = SslLabsClient(
            SslLabsClientSettings(
                api_base_url="https://api.ssllabs.com/api/v4",
                email="security@example.com",
            ),
            transport=_transport(handler),
        )

        try:
            with self.assertRaises(SslLabsRetryableError) as exc:
                await client.analyze(host="example.com", start_new=True)
        finally:
            await client.aclose()

        self.assertEqual(exc.exception.retry_after_seconds, 3600)


class SslLabsHostValidationTests(unittest.TestCase):
    def test_validate_ssllabs_host_rejects_internal_domain(self) -> None:
        with self.assertRaisesRegex(ValueError, "public hostname"):
            validate_ssllabs_host("internal.service.local")

    def test_validate_ssllabs_host_accepts_public_domain(self) -> None:
        self.assertEqual(validate_ssllabs_host("Example.COM."), "example.com")

    def test_validate_ssllabs_host_rejects_urls_ports_paths_and_invalid_labels(self) -> None:
        for raw_value in (
            "example.com:443",
            "https://example.com",
            "example .com",
            "-bad.example.com",
            "bad..example.com",
            "example.com/path",
        ):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(ValueError, "hostname|public hostname"):
                    validate_ssllabs_host(raw_value)

    def test_validate_ssllabs_host_rejects_wildcards_and_ip_addresses(self) -> None:
        for raw_value in ("*.example.com", "127.0.0.1"):
            with self.subTest(raw_value=raw_value):
                with self.assertRaisesRegex(ValueError, "public hostname"):
                    validate_ssllabs_host(raw_value)

    def test_next_schedule_time_normalizes_naive_reference_to_utc(self) -> None:
        scheduled = next_schedule_time("weekly", datetime(2026, 5, 27, 12, 0, 0))

        self.assertEqual(scheduled.tzinfo, UTC)
        self.assertEqual(scheduled, datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC))

    def test_next_schedule_time_applies_deterministic_jitter_when_requested(self) -> None:
        reference = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)

        scheduled_one = next_schedule_time(
            "weekly",
            reference,
            jitter_key="1:example.com",
            max_jitter=timedelta(minutes=15),
        )
        scheduled_two = next_schedule_time(
            "weekly",
            reference,
            jitter_key="1:example.com",
            max_jitter=timedelta(minutes=15),
        )

        base = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
        self.assertEqual(scheduled_one, scheduled_two)
        self.assertGreaterEqual(scheduled_one, base)
        self.assertLessEqual(scheduled_one, base + timedelta(minutes=15))

    def test_schedule_interval_rejects_unsupported_frequency(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported SSL Labs schedule frequency"):
            schedule_interval("yearly")  # type: ignore[arg-type]

    def test_status_badge_class_prefers_failure_status_over_stale_grade(self) -> None:
        self.assertEqual(status_badge_class("failed", "A+"), "status-pill--offline")


class SslLabsRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self._temp_dir.name) / "ssllabs.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._temp_dir.cleanup()

    async def test_get_active_scan_for_target_ignores_stale_active_rows(self) -> None:
        now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)

        async with self.session_factory() as session:
            site = Site(site_name="Example", domain="example.com", upstream_url="https://backend.example.com")
            session.add(site)
            await session.flush()

            target = SslLabsTarget(site_id=site.id, host="example.com")
            session.add(target)
            await session.flush()

            stale_scan = SslLabsScan(
                target_id=target.id,
                site_id=site.id,
                host=target.host,
                status="rate_limited",
                started_at=now - ACTIVE_SCAN_STALE_AFTER - ACTIVE_SCAN_STALE_AFTER,
                next_poll_at=now - ACTIVE_SCAN_STALE_AFTER - ACTIVE_SCAN_STALE_AFTER,
            )
            session.add(stale_scan)
            await session.commit()

        async with self.session_factory() as session:
            active_scan = await ssllabs_repository.get_active_scan_for_target(session, target.id, now=now)

        self.assertIsNone(active_scan)

    async def test_create_scan_if_none_active_returns_existing_state_instead_of_duplicating(self) -> None:
        now = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)

        async with self.session_factory() as session:
            site = Site(site_name="Example", domain="example.com", upstream_url="https://backend.example.com")
            session.add(site)
            await session.flush()

            target = SslLabsTarget(site_id=site.id, host="example.com")
            session.add(target)
            await session.flush()

            active_scan = SslLabsScan(
                target_id=target.id,
                site_id=site.id,
                host=target.host,
                status="queued",
                started_at=now,
            )
            session.add(active_scan)
            await session.commit()

        async with self.session_factory() as session:
            persisted_target = await ssllabs_repository.get_target_by_id(session, target.id)
            self.assertIsNotNone(persisted_target)
            duplicate = await ssllabs_repository.create_scan_if_none_active(
                session,
                persisted_target,
                status="queued",
                now=now,
            )
            await session.rollback()

        self.assertIsNone(duplicate)

    async def test_create_scan_if_none_active_ignores_stale_active_rows_without_explicit_now(self) -> None:
        stale_started_at = datetime.now(UTC) - ACTIVE_SCAN_STALE_AFTER - ACTIVE_SCAN_STALE_AFTER

        async with self.session_factory() as session:
            site = Site(site_name="Example", domain="example.com", upstream_url="https://backend.example.com")
            session.add(site)
            await session.flush()

            target = SslLabsTarget(site_id=site.id, host="example.com")
            session.add(target)
            await session.flush()

            session.add(
                SslLabsScan(
                    target_id=target.id,
                    site_id=site.id,
                    host=target.host,
                    status="rate_limited",
                    started_at=stale_started_at,
                    next_poll_at=stale_started_at,
                )
            )
            await session.commit()

        async with self.session_factory() as session:
            persisted_target = await ssllabs_repository.get_target_by_id(session, target.id)
            self.assertIsNotNone(persisted_target)
            created = await ssllabs_repository.create_scan_if_none_active(session, persisted_target)
            await session.rollback()

        self.assertIsNotNone(created)

    async def test_sync_targets_skips_sites_without_https(self) -> None:
        async with self.session_factory() as session:
            https_site = Site(
                site_name="Example",
                domain="example.com",
                upstream_url="https://backend.example.com",
                caddy_directives="reverse_proxy localhost:8080",
                enabled=True,
            )
            http_only_site = Site(
                site_name="Plain",
                domain="plain.example.com",
                upstream_url="http://backend.example.com",
                caddy_directives="tls off\nreverse_proxy localhost:8080",
                enabled=True,
            )
            disabled_site = Site(
                site_name="Disabled",
                domain="disabled.example.com",
                upstream_url="https://backend-disabled.example.com",
                caddy_directives="reverse_proxy localhost:8080",
                enabled=False,
            )
            session.add_all([https_site, http_only_site, disabled_site])
            await session.flush()

            synced = await ssllabs_repository.sync_targets(session, [https_site, http_only_site, disabled_site])
            await session.commit()

        self.assertEqual([target.host for target in synced], ["example.com"])

    async def test_sync_targets_keeps_targets_with_active_scans_until_scan_completes(self) -> None:
        now = datetime.now(UTC)

        async with self.session_factory() as session:
            site = Site(site_name="Example", domain="example.com", upstream_url="https://backend.example.com")
            session.add(site)
            await session.flush()

            target = SslLabsTarget(site_id=site.id, host="example.com")
            session.add(target)
            await session.flush()

            session.add(
                SslLabsScan(
                    target_id=target.id,
                    site_id=site.id,
                    host=target.host,
                    status="queued",
                    started_at=now,
                )
            )
            await session.commit()

        async with self.session_factory() as session:
            synced = await ssllabs_repository.sync_targets(session, [])
            await session.commit()

        self.assertEqual([target.host for target in synced], ["example.com"])

    async def test_list_due_targets_rejects_invalid_limit(self) -> None:
        async with self.session_factory() as session:
            with self.assertRaisesRegex(ValueError, "Limit must be between 1 and 500"):
                await ssllabs_repository.list_due_targets(session, now=datetime.now(UTC), limit=0)


class SslLabsHttpsEligibilityTests(unittest.TestCase):
    def test_site_uses_https_rejects_disabled_or_tls_off_sites(self) -> None:
        self.assertTrue(
            site_uses_https(
                Site(
                    site_name="Example",
                    domain="example.com",
                    upstream_url="https://backend.example.com",
                    caddy_directives="reverse_proxy localhost:8080",
                    enabled=True,
                )
            )
        )
        self.assertFalse(
            site_uses_https(
                Site(
                    site_name="Plain",
                    domain="plain.example.com",
                    upstream_url="http://backend.example.com",
                    caddy_directives="tls off\nreverse_proxy localhost:8080",
                    enabled=True,
                )
            )
        )
        self.assertFalse(
            site_uses_https(
                Site(
                    site_name="Disabled",
                    domain="disabled.example.com",
                    upstream_url="https://backend-disabled.example.com",
                    caddy_directives="reverse_proxy localhost:8080",
                    enabled=False,
                )
            )
        )


class SslLabsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self._temp_dir.name) / "ssllabs-service.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
        self.service = SslLabsService()

    async def asyncTearDown(self) -> None:
        await self.service.shutdown()
        await self.engine.dispose()
        self._temp_dir.cleanup()

    async def _create_scan_state(self) -> tuple[int, int]:
        async with self.session_factory() as session:
            site = Site(site_name="Example", domain="example.com", upstream_url="https://backend.example.com")
            session.add(site)
            await session.flush()

            target = SslLabsTarget(site_id=site.id, host="example.com", schedule_frequency="weekly")
            session.add(target)
            await session.flush()

            scan = SslLabsScan(
                target_id=target.id,
                site_id=site.id,
                host=target.host,
                status="queued",
            )
            session.add(scan)
            await session.commit()
            return target.id, scan.id

    async def test_mark_scan_state_ignores_event_publish_failures(self) -> None:
        target_id, scan_id = await self._create_scan_state()

        with (
            patch("app.services.ssllabs.get_session_factory", return_value=self.session_factory),
            patch("app.services.events.event_bus.publish", new=AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            await self.service._mark_scan_state(
                target_id=target_id,
                scan_id=scan_id,
                payload={"host": "example.com"},
                status="queued",
            )

        async with self.session_factory() as session:
            scan = await ssllabs_repository.get_scan_by_id(session, scan_id)

        self.assertIsNotNone(scan)
        self.assertEqual(scan.status, "queued")

    async def test_run_scan_does_not_auto_register_email(self) -> None:
        target_id, scan_id = await self._create_scan_state()

        with (
            patch("app.services.ssllabs.get_session_factory", return_value=self.session_factory),
            patch(
                "app.services.ssllabs.get_settings",
                return_value=SimpleNamespace(
                    ssllabs_api_base_url="https://api.ssllabs.com/api/v4",
                    ssllabs_timeout_seconds=20.0,
                    ssllabs_cache_max_age_hours=24,
                ),
            ),
            patch("app.services.ssllabs.get_ssllabs_email", new=AsyncMock(return_value="team@example.com")),
            patch("app.services.ssllabs.try_publish_resource_event", new=AsyncMock()),
            patch(
                "app.services.ssllabs.SslLabsClient.analyze",
                new=AsyncMock(side_effect=SslLabsEmailNotRegisteredError("not registered")),
            ),
            patch("app.services.ssllabs.register_email_with_ssllabs", new=AsyncMock()) as register_email,
        ):
            await self.service._run_scan(target_id=target_id, scan_id=scan_id, force_new=True)

        register_email.assert_not_awaited()

        async with self.session_factory() as session:
            scan = await ssllabs_repository.get_scan_by_id(session, scan_id)

        self.assertIsNotNone(scan)
        self.assertEqual(scan.status, "failed")
        self.assertEqual(scan.error_code, "SslLabsClientError")
        self.assertIn("not registered", scan.error_message.lower())

    async def test_startup_creates_scheduler_task_even_without_configured_email(self) -> None:
        class _FakeTask:
            def cancel(self) -> None:
                return None

        fake_task = _FakeTask()

        def _create_task(coro, *, name=None):
            del name
            coro.close()
            return fake_task

        with patch("app.services.ssllabs.asyncio.create_task", side_effect=_create_task) as create_task:
            await self.service.startup()

        self.assertIs(self.service._scheduler_task, fake_task)
        create_task.assert_called_once()
        self.service._scheduler_task = None