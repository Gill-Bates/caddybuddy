#!/usr/bin/env python3
#
# tests/test_ssllabs_service.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
import tempfile

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.entities import Site, SslLabsScan, SslLabsTarget
from app.repositories.ssllabs import ACTIVE_SCAN_STALE_AFTER, ssllabs_repository
from app.services.ssllabs import SslLabsClient, SslLabsClientSettings, SslLabsRetryableError
from app.utils.ssllabs import next_schedule_time, schedule_interval, status_badge_class, validate_ssllabs_host


def _transport(handler):
    return httpx.MockTransport(handler)


class SslLabsClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_adds_registered_email_header(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["email"], "security@example.com")
            self.assertEqual(request.url.path, "/api/v4/analyze")
            return httpx.Response(200, json={"status": "READY", "host": "example.com"})

        client = SslLabsClient(
            SslLabsClientSettings(
                api_base_url="https://api.ssllabs.com/api/v4",
                email="security@example.com",
            ),
            transport=_transport(handler),
        )

        payload = await client.analyze(host="example.com", from_cache=True, max_age_hours=24)
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

        with self.assertRaises(SslLabsRetryableError) as exc:
            await client.analyze(host="example.com", start_new=True)

        await client.aclose()
        self.assertEqual(exc.exception.status_code, 429)
        self.assertGreaterEqual(exc.exception.retry_after_seconds, 901)


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
            site = Site(domain="example.com", upstream_url="https://backend.example.com")
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
            site = Site(domain="example.com", upstream_url="https://backend.example.com")
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