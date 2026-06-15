#!/usr/bin/env python3
#
# tests/test_ssllabs_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
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
from app.models.entities import Site, SslLabsRankHistory, SslLabsScan, SslLabsTarget
from app.repositories.ssllabs import ACTIVE_SCAN_STALE_AFTER, site_uses_https, ssllabs_repository
from app.services.ssllabs import (
    SslLabsClient,
    SslLabsClientError,
    SslLabsClientSettings,
    SslLabsEmailNotRegisteredError,
    SslLabsRetryableError,
    SslLabsService,
    build_rank_history,
    check_email_registration_status,
    register_email_with_ssllabs,
    resolve_history_range,
)
from app.utils.ssllabs import (
    grade_to_rank,
    next_schedule_time,
    parse_ssllabs_schedule_control,
    schedule_interval,
    status_badge_class,
    validate_ssllabs_host,
)


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

    async def test_client_rejects_oversized_response_before_json_decode(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"x" * (2 * 1024 * 1024 + 1),
                headers={"content-type": "application/json"},
            )

        client = SslLabsClient(
            SslLabsClientSettings(
                api_base_url="https://api.ssllabs.com/api/v4",
                email="security@example.com",
            ),
            transport=_transport(handler),
        )

        try:
            with self.assertRaisesRegex(SslLabsClientError, "response is too large"):
                await client.analyze(host="example.com")
        finally:
            await client.aclose()

    async def test_client_400_does_not_log_response_body(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="unexpected secret token")

        client = SslLabsClient(
            SslLabsClientSettings(
                api_base_url="https://api.ssllabs.com/api/v4",
                email="security@example.com",
            ),
            transport=_transport(handler),
        )

        try:
            with patch("app.services.ssllabs.logger.warning") as warning:
                with self.assertRaises(SslLabsClientError):
                    await client.analyze(host="example.com")
        finally:
            await client.aclose()

        warning.assert_called_once_with("SSL Labs rejected request with HTTP 400.")

    async def test_client_info_maps_unregistered_400_body_to_dedicated_error(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="This email is not registered.")

        client = SslLabsClient(
            SslLabsClientSettings(
                api_base_url="https://api.ssllabs.com/api/v4",
                email="security@example.com",
            ),
            transport=_transport(handler),
        )

        try:
            with self.assertRaises(SslLabsEmailNotRegisteredError):
                await client.info()
        finally:
            await client.aclose()

    async def test_check_email_registration_status_rejects_invalid_api_base_url(self) -> None:
        with self.assertRaisesRegex(SslLabsClientError, "must not include query or fragment"):
            await check_email_registration_status(
                "security@example.com",
                api_base_url="https://api.ssllabs.com/api/v4?debug=1",
                use_cache=False,
            )

    async def test_normalize_url_accepts_trailing_slash_root(self) -> None:
        # https://api.ssllabs.com/ must be normalised to /api/v4, not left as "/".
        with patch("app.services.ssllabs.SslLabsClient.info", new=AsyncMock(return_value={})):
            result = await check_email_registration_status(
                "security@example.com",
                api_base_url="https://api.ssllabs.com/",
                use_cache=False,
            )
        self.assertTrue(result)

    async def test_normalize_url_rejects_wrong_path(self) -> None:
        with self.assertRaisesRegex(SslLabsClientError, "path must be /api/v4"):
            await check_email_registration_status(
                "security@example.com",
                api_base_url="https://api.ssllabs.com/api/v3",
                use_cache=False,
            )

    async def test_normalize_url_accepts_bare_host(self) -> None:
        with patch("app.services.ssllabs.SslLabsClient.info", new=AsyncMock(return_value={})):
            result = await check_email_registration_status(
                "security@example.com",
                api_base_url="https://api.ssllabs.com",
                use_cache=False,
            )
        self.assertTrue(result)

    async def test_check_email_registration_status_uses_client_info(self) -> None:
        with patch("app.services.ssllabs.SslLabsClient.info", new=AsyncMock(return_value={"status": "ok"})) as info:
            result = await check_email_registration_status(
                "security@example.com",
                api_base_url="https://api.ssllabs.com/api/v4",
                use_cache=False,
            )

        self.assertTrue(result)
        info.assert_awaited_once()

    async def test_register_email_with_ssllabs_rejects_invalid_api_base_url(self) -> None:
        with self.assertRaisesRegex(SslLabsClientError, "must use HTTPS"):
            await register_email_with_ssllabs(
                "security@example.com",
                api_base_url="http://api.ssllabs.com/api/v4",
            )

    async def test_register_email_with_ssllabs_rejects_unapproved_host(self) -> None:
        with self.assertRaisesRegex(SslLabsClientError, "host is not allowed"):
            await register_email_with_ssllabs(
                "security@example.com",
                api_base_url="https://example.com/api/v4",
            )

    async def test_register_email_with_ssllabs_uses_client_register(self) -> None:
        with patch("app.services.ssllabs.SslLabsClient.register", new=AsyncMock(return_value={"status": "success"})) as register:
            result = await register_email_with_ssllabs(
                "security@example.com",
                api_base_url="https://api.ssllabs.com/api/v4",
            )

        self.assertTrue(result)
        register.assert_awaited_once()


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

    def test_next_schedule_time_rejects_monthly(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported SSL Labs schedule frequency"):
            next_schedule_time("monthly", datetime(2026, 1, 31, 12, 0, 0, tzinfo=UTC))  # type: ignore[arg-type]

    def test_schedule_interval_rejects_unsupported_frequency(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported SSL Labs schedule frequency"):
            schedule_interval("yearly")  # type: ignore[arg-type]

    def test_status_badge_class_prefers_failure_status_over_stale_grade(self) -> None:
        self.assertEqual(status_badge_class("failed", "A+"), "status-pill--offline")

    def test_schedule_control_rejects_monthly(self) -> None:
        with self.assertRaises(ValueError):
            parse_ssllabs_schedule_control("monthly")


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

    async def test_rank_history_record_list_and_prune(self) -> None:
        async with self.session_factory() as session:
            await ssllabs_repository.record_rank_history(
                session, host="b.example.com", grade="A", rank=6,
                recorded_at=datetime(2026, 6, 1, tzinfo=UTC),
            )
            await ssllabs_repository.record_rank_history(
                session, host="a.example.com", grade="A+", rank=7,
                recorded_at=datetime(2026, 6, 2, tzinfo=UTC),
            )
            await ssllabs_repository.record_rank_history(
                session, host="a.example.com", grade="B", rank=4,
                recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            await session.commit()

        async with self.session_factory() as session:
            rows = await ssllabs_repository.list_rank_history_since(
                session, since=datetime(2026, 5, 1, tzinfo=UTC)
            )
        # Ordered by host then recorded_at; the January row is outside the window.
        self.assertEqual([(r.host, r.grade) for r in rows], [("a.example.com", "A+"), ("b.example.com", "A")])

        async with self.session_factory() as session:
            removed = await ssllabs_repository.prune_rank_history_older_than(
                session, cutoff=datetime(2026, 5, 1, tzinfo=UTC)
            )
            await session.commit()
        self.assertEqual(removed, 1)

    async def test_list_rank_history_since_requires_aware_datetime(self) -> None:
        async with self.session_factory() as session:
            with self.assertRaisesRegex(ValueError, "timezone-aware"):
                await ssllabs_repository.list_rank_history_since(session, since=datetime(2026, 6, 1))

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

    async def _all_rank_history(self) -> list[SslLabsRankHistory]:
        async with self.session_factory() as session:
            since = datetime(2000, 1, 1, tzinfo=UTC)
            return await ssllabs_repository.list_rank_history_since(session, since=since)

    async def test_mark_scan_state_records_rank_history_on_completion(self) -> None:
        target_id, scan_id = await self._create_scan_state()

        with (
            patch("app.services.ssllabs.get_session_factory", return_value=self.session_factory),
            patch("app.services.events.event_bus.publish", new=AsyncMock()),
        ):
            await self.service._mark_scan_state(
                target_id=target_id,
                scan_id=scan_id,
                payload={"host": "example.com", "grade": "A+", "endpoints": [{"grade": "A+"}]},
                status="ready",
                completed=True,
            )

        history = await self._all_rank_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].host, "example.com")
        self.assertEqual(history[0].grade, "A+")
        self.assertEqual(history[0].rank, 7)

    async def test_mark_scan_state_records_trust_failure_history(self) -> None:
        target_id, scan_id = await self._create_scan_state()

        with (
            patch("app.services.ssllabs.get_session_factory", return_value=self.session_factory),
            patch("app.services.events.event_bus.publish", new=AsyncMock()),
        ):
            await self.service._mark_scan_state(
                target_id=target_id,
                scan_id=scan_id,
                payload={"host": "example.com", "grade": "T"},
                status="ready",
                completed=True,
            )

        history = await self._all_rank_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].grade, "T")
        self.assertEqual(history[0].rank, -1)

    async def test_mark_scan_state_skips_history_without_grade(self) -> None:
        target_id, scan_id = await self._create_scan_state()

        with (
            patch("app.services.ssllabs.get_session_factory", return_value=self.session_factory),
            patch("app.services.events.event_bus.publish", new=AsyncMock()),
        ):
            await self.service._mark_scan_state(
                target_id=target_id,
                scan_id=scan_id,
                payload={"host": "example.com"},
                status="failed",
                completed=True,
            )

        self.assertEqual(await self._all_rank_history(), [])

    async def test_prune_rank_history_honors_retention(self) -> None:
        async with self.session_factory() as session:
            session.add_all([
                SslLabsRankHistory(host="a.example.com", grade="A+", rank=7,
                                   recorded_at=datetime(2026, 1, 1, tzinfo=UTC)),
                SslLabsRankHistory(host="a.example.com", grade="A", rank=6,
                                   recorded_at=datetime(2026, 6, 1, tzinfo=UTC)),
            ])
            await session.commit()

        now = datetime(2026, 6, 14, tzinfo=UTC)
        with (
            patch("app.services.ssllabs.get_session_factory", return_value=self.session_factory),
            patch("app.services.ssllabs.get_ssllabs_history_retention_days", new=AsyncMock(return_value=30)),
        ):
            async with self.session_factory() as session:
                removed = await self.service.prune_rank_history(session, now=now)
                await session.commit()

        self.assertEqual(removed, 1)
        remaining = await self._all_rank_history()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].grade, "A")

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
        self.assertEqual(scan.error_code, "SslLabsEmailNotRegisteredError")
        self.assertEqual(scan.error_message, "SSL Labs email is not registered.")

    async def test_startup_creates_scheduler_task_even_without_configured_email(self) -> None:
        class _FakeTask:
            def done(self) -> bool:
                return False

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

    async def test_startup_restarts_scheduler_when_previous_task_is_done(self) -> None:
        class _CompletedTask:
            def done(self) -> bool:
                return True

            def cancel(self) -> None:
                return None

        class _FakeTask:
            def done(self) -> bool:
                return False

            def cancel(self) -> None:
                return None

        completed_task = _CompletedTask()
        replacement_task = _FakeTask()

        def _create_task(coro, *, name=None):
            del name
            coro.close()
            return replacement_task

        self.service._scheduler_task = completed_task
        with patch("app.services.ssllabs.asyncio.create_task", side_effect=_create_task) as create_task:
            await self.service.startup()

        self.assertIs(self.service._scheduler_task, replacement_task)
        create_task.assert_called_once()
        self.service._scheduler_task = None

    async def test_request_scan_cancels_task_when_tracking_registration_fails(self) -> None:
        async with self.session_factory() as session:
            site = Site(site_name="Example", domain="example.com", upstream_url="https://backend.example.com")
            session.add(site)
            await session.flush()

            target = SslLabsTarget(site_id=site.id, host="example.com")
            session.add(target)
            await session.commit()
            target_id = target.id

        class _FakeTask:
            def __init__(self) -> None:
                self.cancelled = False

            def add_done_callback(self, _callback) -> None:
                raise RuntimeError("callback registration failed")

            def cancel(self) -> None:
                self.cancelled = True

        fake_task = _FakeTask()
        real_create_task = asyncio.create_task

        def create_task_side_effect(coro, *, name=None):
            if name == f"ssllabs-scan-{target_id}":
                coro.close()
                return fake_task
            return real_create_task(coro, name=name)

        with (
            patch("app.services.ssllabs.get_session_factory", return_value=self.session_factory),
            patch(
                "app.services.ssllabs.get_settings",
                return_value=SimpleNamespace(
                    ssllabs_api_base_url="https://api.ssllabs.com/api/v4",
                    ssllabs_timeout_seconds=20.0,
                ),
            ),
            patch("app.services.ssllabs.get_ssllabs_email", new=AsyncMock(return_value="team@example.com")),
            patch("app.services.ssllabs.asyncio.create_task", side_effect=create_task_side_effect),
        ):
            with self.assertRaisesRegex(RuntimeError, "callback registration failed"):
                await self.service.request_scan(target_id=target_id, force_new=True)

        self.assertTrue(fake_task.cancelled)
        self.assertNotIn(target_id, self.service._active_tasks)


class GradeRankTests(unittest.TestCase):
    def test_grade_to_rank_orders_grades(self) -> None:
        self.assertEqual(grade_to_rank("A+"), 7)
        self.assertEqual(grade_to_rank("a"), 6)
        self.assertEqual(grade_to_rank("A-"), 5)
        self.assertEqual(grade_to_rank("F"), 0)
        self.assertEqual(grade_to_rank("T"), -1)
        self.assertEqual(grade_to_rank("M"), -1)

    def test_grade_to_rank_returns_none_for_unknown(self) -> None:
        self.assertIsNone(grade_to_rank(None))
        self.assertIsNone(grade_to_rank("Z"))

    def test_resolve_history_range_defaults_for_unknown(self) -> None:
        self.assertEqual(resolve_history_range("90d"), ("90d", 90))
        self.assertEqual(resolve_history_range("1y"), ("1y", 365))
        self.assertEqual(resolve_history_range("2y"), ("2y", 730))
        self.assertEqual(resolve_history_range("7d"), ("30d", 30))  # removed; falls back
        self.assertEqual(resolve_history_range("bogus"), ("30d", 30))
        self.assertEqual(resolve_history_range(None), ("30d", 30))


class SslLabsRankHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self._temp_dir.name) / "ssllabs.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self._temp_dir.cleanup()

    async def _seed_history(self, session, *, host, grade, rank, recorded_at):
        entry = SslLabsRankHistory(host=host, grade=grade, rank=rank, recorded_at=recorded_at)
        session.add(entry)
        await session.flush()
        return entry

    async def test_build_rank_history_buckets_latest_grade_per_week(self) -> None:
        now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            # 2026-06-10 is Wednesday — week starts Monday 2026-06-08.
            await self._seed_history(
                session, host="example.com", grade="A", rank=6,
                recorded_at=datetime(2026, 6, 10, 8, 0, tzinfo=UTC),
            )
            # Same host, same week, later sample with a better grade wins.
            await self._seed_history(
                session, host="example.com", grade="A+", rank=7,
                recorded_at=datetime(2026, 6, 10, 20, 0, tzinfo=UTC),
            )
            await session.commit()

        async with self.session_factory() as session:
            history = await build_rank_history(session, range_key="30d", now=now)

        self.assertEqual(history.range_key, "30d")
        self.assertEqual(len(history.series), 1)
        series = history.series[0]
        self.assertEqual(series.host, "example.com")
        self.assertEqual(len(series.points), 1)
        self.assertEqual(series.points[0].grade, "A+")
        self.assertEqual(series.points[0].rank, 7)
        self.assertEqual(series.points[0].date, "2026-06-08")  # Monday of that week

    async def test_build_rank_history_merges_samples_from_same_week(self) -> None:
        now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            # Tuesday and Thursday of the same week (Mon 2026-06-08) → one bucket.
            await self._seed_history(
                session, host="b.example.com", grade="B", rank=4,
                recorded_at=datetime(2026, 6, 9, 10, 0, tzinfo=UTC),
            )
            await self._seed_history(
                session, host="b.example.com", grade="A", rank=6,
                recorded_at=datetime(2026, 6, 11, 10, 0, tzinfo=UTC),
            )
            await session.commit()

        async with self.session_factory() as session:
            history = await build_rank_history(session, range_key="30d", now=now)

        self.assertEqual(len(history.series), 1)
        series = history.series[0]
        self.assertEqual(len(series.points), 1, "two samples from the same week must be merged")
        self.assertEqual(series.points[0].grade, "A")
        self.assertEqual(series.points[0].date, "2026-06-08")

    async def test_build_rank_history_excludes_samples_outside_range(self) -> None:
        now = datetime(2026, 6, 13, 12, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            await self._seed_history(
                session, host="old.example.com", grade="B", rank=4,
                recorded_at=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
            )
            await session.commit()

        async with self.session_factory() as session:
            history = await build_rank_history(session, range_key="30d", now=now)

        self.assertEqual(history.series, [])
