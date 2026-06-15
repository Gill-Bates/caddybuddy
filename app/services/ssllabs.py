#!/usr/bin/env python3
#
# app/services/ssllabs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings, get_settings
from app.database.session import get_session_factory
from app.repositories.sites import site_repository
from app.repositories.ssllabs import ssllabs_repository
from app.schemas.ssllabs import SslLabsScanStatus, SslLabsScheduleFrequency
from app.services.events import try_publish_resource_event
from app.services.runtime_settings import get_ssllabs_email, get_ssllabs_history_retention_days
from app.utils.ssllabs import (
    GRADE_RANKS,
    grade_to_rank,
    is_ssllabs_scan_terminal,
    mask_email,
    next_schedule_time,
    ssllabs_scan_event_action,
    validate_ssllabs_host,
)


logger = logging.getLogger(__name__)

# Quick-filter presets for the dashboard SSL Labs rank chart.
SSLLABS_HISTORY_RANGES: dict[str, int] = {
    "30d": 30,
    "90d": 90,
    "180d": 180,
    "1y": 365,
    "2y": 730,
}
SSLLABS_HISTORY_DEFAULT_RANGE = "30d"

_INITIAL_POLL_SECONDS = 5
_RUNNING_POLL_SECONDS = 10
_SCHEDULER_POLL_SECONDS = 60
_MIN_SECONDS_BETWEEN_NEW_SCANS = 300
_MAX_RETRY_AFTER_SECONDS = 60 * 60
# Spread weekly scheduled scans across a full day (deterministic per host) so a large
# fleet does not fire at the SSL Labs API simultaneously.
_WEEKLY_SCHEDULE_JITTER = timedelta(hours=24)
_MAX_SCAN_DURATION_SECONDS = 2 * 60 * 60
_MAX_SSL_LABS_RESPONSE_BYTES = 2 * 1024 * 1024
_ALLOWED_SSL_LABS_API_HOST = "api.ssllabs.com"


@dataclass(slots=True, frozen=True)
class SslLabsClientSettings:
    api_base_url: str
    email: str
    timeout_seconds: float = 20.0


@dataclass(slots=True, frozen=True)
class SslLabsScanRequestResult:
    scan_id: int
    host: str
    created: bool
    status: SslLabsScanStatus


class SslLabsClientError(RuntimeError):
    pass


class SslLabsEmailNotRegisteredError(SslLabsClientError):
    """Raised when the email is not yet registered with SSL Labs."""

    pass


class SslLabsRetryableError(SslLabsClientError):
    def __init__(self, message: str, *, retry_after_seconds: int, status_code: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.status_code = status_code


class SslLabsServiceError(RuntimeError):
    pass


def _normalize_ssllabs_api_base_url(api_base_url: str) -> str:
    normalized = api_base_url.strip()
    if not normalized:
        raise SslLabsClientError("SSL Labs API base URL must not be empty.")

    parsed = urlsplit(normalized)
    if parsed.scheme != "https":
        raise SslLabsClientError("SSL Labs API base URL must use HTTPS.")
    if parsed.username or parsed.password:
        raise SslLabsClientError("SSL Labs API base URL must not include username or password.")
    if not parsed.hostname:
        raise SslLabsClientError("SSL Labs API base URL must include a host.")
    if parsed.query or parsed.fragment:
        raise SslLabsClientError("SSL Labs API base URL must not include query or fragment.")
    if parsed.hostname.lower() != _ALLOWED_SSL_LABS_API_HOST:
        raise SslLabsClientError("SSL Labs API host is not allowed.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise SslLabsClientError("SSL Labs API base URL has an invalid port.") from exc

    host = parsed.hostname
    if host is None:
        raise SslLabsClientError("SSL Labs API base URL must include a host.")
    if ":" in host:
        host = f"[{host}]"

    raw_path = parsed.path.strip().strip("/")
    path = f"/{raw_path}" if raw_path else "/api/v4"
    if path.rstrip("/") != "/api/v4":
        raise SslLabsClientError("SSL Labs API base URL path must be /api/v4.")
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit(("https", netloc, path, "", ""))


def _next_scheduled_at_for_target(target, reference: datetime) -> datetime | None:
    frequency = getattr(target, "schedule_frequency", None)
    if frequency is None:
        return None

    jitter_key = f"{getattr(target, 'site_id', 'unknown')}:{getattr(target, 'host', 'unknown')}"
    return next_schedule_time(
        frequency,
        reference,
        jitter_key=jitter_key,
        max_jitter=_WEEKLY_SCHEDULE_JITTER,
    )


def _retry_after_seconds(response: httpx.Response, fallback: int) -> int:
    raw_value = response.headers.get("Retry-After")
    if raw_value is None:
        return fallback
    try:
        parsed = int(raw_value)
    except ValueError:
        return fallback
    retry_after_seconds = min(max(parsed, fallback), _MAX_RETRY_AFTER_SECONDS)
    if retry_after_seconds != parsed:
        logger.info(
            "Capped SSL Labs Retry-After from %s to %s seconds",
            parsed,
            retry_after_seconds,
        )
    return retry_after_seconds


def _poll_delay_for_status(status: str) -> int:
    return _RUNNING_POLL_SECONDS if status == "in_progress" else _INITIAL_POLL_SECONDS


def _extract_grade(payload: dict[str, Any]) -> str | None:
    top_level_grade = payload.get("grade")
    if isinstance(top_level_grade, str) and top_level_grade.strip():
        return top_level_grade.strip()[:8]

    endpoint_grades: list[str] = []
    endpoints = payload.get("endpoints")
    if isinstance(endpoints, list):
        for endpoint in endpoints:
            if not isinstance(endpoint, dict):
                continue
            grade = endpoint.get("grade")
            if isinstance(grade, str) and grade.strip():
                endpoint_grades.append(grade.strip()[:8])

    unique_grades = list(dict.fromkeys(endpoint_grades))
    if len(unique_grades) == 1:
        return unique_grades[0]
    if len(unique_grades) > 1:
        return "Mixed"
    return None


def _extract_error_message(payload: dict[str, Any]) -> str | None:
    for key in ("statusMessage", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    errors = payload.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                message = item.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
    return None


def _map_remote_status(payload: dict[str, Any]) -> SslLabsScanStatus:
    raw_status = payload.get("status")
    if not isinstance(raw_status, str):
        return "starting"
    normalized = raw_status.strip().upper()
    if normalized == "READY":
        return "ready"
    if normalized == "ERROR":
        return "error"
    if normalized == "DNS":
        return "dns"
    if normalized == "IN_PROGRESS":
        return "in_progress"
    return "starting"


# Registration status cache: {normalised_email: (is_registered, timestamp)}
_registration_status_cache: dict[str, tuple[bool | None, datetime]] = {}
_REGISTRATION_CACHE_TTL_SECONDS = 300  # 5 minutes
_REGISTRATION_CACHE_MAX_ENTRIES = 32


def _registration_cache_key(email: str) -> str:
    return email.strip().casefold()


async def check_email_registration_status(
    email: str,
    *,
    api_base_url: str = "https://api.ssllabs.com/api/v4",
    use_cache: bool = True,
) -> bool | None:
    """
    Check if an email is registered with SSL Labs API v4.

    Uses a TTL cache to avoid excessive API calls.
    Returns True if registered, False if explicitly unregistered, else None.
    """
    now = datetime.now(UTC)
    key = _registration_cache_key(email)

    if use_cache and key in _registration_status_cache:
        is_registered, cached_at = _registration_status_cache[key]
        age_seconds = (now - cached_at).total_seconds()
        if age_seconds < _REGISTRATION_CACHE_TTL_SECONDS:
            return is_registered

    client = SslLabsClient(
        SslLabsClientSettings(
            api_base_url=api_base_url,
            email=email,
        )
    )
    try:
        await client.info()
        if len(_registration_status_cache) >= _REGISTRATION_CACHE_MAX_ENTRIES:
            oldest = min(_registration_status_cache, key=lambda k: _registration_status_cache[k][1])
            _registration_status_cache.pop(oldest, None)
        _registration_status_cache[key] = (True, now)
        return True
    except SslLabsEmailNotRegisteredError:
        _registration_status_cache[key] = (False, now)
        return False
    except SslLabsClientError as exc:
        logger.warning("Failed to check SSL Labs registration status: %s", exc)
        return None
    finally:
        await client.aclose()


def clear_registration_status_cache(email: str | None = None) -> None:
    """Clear the registration status cache for a specific email or all emails."""
    if email is None:
        _registration_status_cache.clear()
    else:
        _registration_status_cache.pop(_registration_cache_key(email), None)


async def register_email_with_ssllabs(
    email: str,
    *,
    api_base_url: str = "https://api.ssllabs.com/api/v4",
    organization: str = "CaddyBuddy User",
) -> bool:
    """
    Register an email address with SSL Labs API v4.

    Returns True if registration succeeded, False otherwise.
    """
    payload = {
        "firstName": "CaddyBuddy",
        "lastName": "User",
        "email": email,
        "organization": organization,
    }
    client = SslLabsClient(
        SslLabsClientSettings(
            api_base_url=api_base_url,
            email=email,
        )
    )

    try:
        data = await client.register(payload)
        if data.get("status") == "success":
            logger.info("Successfully registered email %s with SSL Labs", mask_email(email))
            clear_registration_status_cache(email)
            return True
        logger.warning("SSL Labs registration failed", extra={"email": mask_email(email)})
        return False
    finally:
        await client.aclose()


class SslLabsClient:
    def __init__(
        self,
        settings: SslLabsClientSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        masked = mask_email(settings.email)
        logger.debug("Initializing SSL Labs client with email: %s", masked)
        normalized_api_base_url = _normalize_ssllabs_api_base_url(settings.api_base_url)
        self._settings = SslLabsClientSettings(
            api_base_url=normalized_api_base_url,
            email=settings.email,
            timeout_seconds=settings.timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=normalized_api_base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(settings.timeout_seconds),
            headers={"email": settings.email},
            follow_redirects=False,
            transport=transport,
        )

    @property
    def settings(self) -> SslLabsClientSettings:
        return self._settings

    async def aclose(self) -> None:
        await self._client.aclose()

    async def info(self) -> dict[str, Any]:
        response = await self._client.get("info")
        if response.status_code in {400, 441}:
            body = response.text.lower()
            if "not registered" in body or "not yet registered" in body or "register api" in body:
                raise SslLabsEmailNotRegisteredError("SSL Labs email is not registered.")
        return self._decode_response(response)

    async def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post("register", json=payload)
        return self._decode_response(response)

    async def analyze(
        self,
        *,
        host: str,
        start_new: bool = False,
        from_cache: bool = False,
        max_age_hours: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {"host": host, "all": "done"}
        if start_new:
            params["startNew"] = "on"
        if from_cache:
            params["fromCache"] = "on"
        if max_age_hours is not None:
            params["maxAge"] = str(max_age_hours)

        response = await self._client.get("analyze", params=params)
        return self._decode_response(response)

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        if len(response.content) > _MAX_SSL_LABS_RESPONSE_BYTES:
            raise SslLabsClientError("SSL Labs response is too large.")
        if response.status_code == 429:
            raise SslLabsRetryableError(
                "SSL Labs rate limit reached.",
                retry_after_seconds=_retry_after_seconds(response, 15 * 60),
                status_code=response.status_code,
            )
        if response.status_code == 503:
            raise SslLabsRetryableError(
                "SSL Labs temporarily unavailable.",
                retry_after_seconds=_retry_after_seconds(response, 15 * 60),
                status_code=response.status_code,
            )
        if response.status_code == 529:
            raise SslLabsRetryableError(
                "SSL Labs is overloaded.",
                retry_after_seconds=_retry_after_seconds(response, 30 * 60),
                status_code=response.status_code,
            )
        if response.status_code == 400:
            body = response.text.lower()
            if "not yet registered" in body or "register api" in body:
                raise SslLabsEmailNotRegisteredError(
                    "SSL Labs email is not yet registered. Registration required."
                )
            logger.warning("SSL Labs rejected request with HTTP 400.")
            raise SslLabsClientError(
                "SSL Labs rejected request (HTTP 400). "
                "Ensure the configured SSL Labs email is valid."
            )
        if response.status_code == 441:
            raise SslLabsEmailNotRegisteredError("SSL Labs email is not registered.")
        if response.status_code >= 400:
            raise SslLabsClientError(f"SSL Labs request failed: HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise SslLabsClientError("SSL Labs returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise SslLabsClientError("SSL Labs returned an invalid response.")
        return payload


class SslLabsService:
    def __init__(self) -> None:
        self._task_lock: asyncio.Lock | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._scheduler_task: asyncio.Task[None] | None = None
        self._active_tasks: dict[int, asyncio.Task[None]] = {}

    def _ensure_task_lock(self) -> asyncio.Lock:
        if self._task_lock is None:
            self._task_lock = asyncio.Lock()
        return self._task_lock

    def _ensure_shutdown_event(self) -> asyncio.Event:
        if self._shutdown_event is None:
            self._shutdown_event = asyncio.Event()
        return self._shutdown_event

    def _client_settings_from(self, settings: Settings, *, email: str) -> SslLabsClientSettings:
        if not email:
            raise SslLabsServiceError("SSL Labs email is not configured.")
        return SslLabsClientSettings(
            api_base_url=_normalize_ssllabs_api_base_url(settings.ssllabs_api_base_url),
            email=email,
            timeout_seconds=settings.ssllabs_timeout_seconds,
        )

    async def masked_email(self) -> str | None:
        session_factory = get_session_factory()
        async with session_factory() as session:
            return mask_email(await get_ssllabs_email(session))

    async def startup(self, settings: Settings | None = None) -> None:
        del settings
        shutdown_event = self._ensure_shutdown_event()
        shutdown_event.clear()
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        self._scheduler_task = asyncio.create_task(self._scheduler_loop(), name="ssllabs-scheduler")

    async def shutdown(self) -> None:
        self._ensure_shutdown_event().set()
        tasks = list(self._active_tasks.values())
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            tasks.append(self._scheduler_task)
            self._scheduler_task = None
        self._active_tasks = {}

        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task

    async def sync_targets(self, session) -> None:
        sites = await site_repository.list_all(session)
        await ssllabs_repository.sync_targets(session, sites)

    async def update_schedule(
        self,
        *,
        target_id: int,
        frequency: SslLabsScheduleFrequency | None,
    ) -> None:
        session_factory = get_session_factory()
        async with session_factory() as session:
            row = await ssllabs_repository.get_target_with_site(session, target_id)
            if row is None:
                raise SslLabsServiceError("SSL Labs target not found.")
            target, _site = row
            if frequency is not None:
                validate_ssllabs_host(target.host)
            target.schedule_frequency = frequency
            target.next_scheduled_at = _next_scheduled_at_for_target(target, datetime.now(UTC))
            await session.commit()

    async def request_scan(
        self,
        *,
        target_id: int,
        force_new: bool,
    ) -> SslLabsScanRequestResult:
        settings = get_settings()
        session_factory = get_session_factory()
        async with session_factory() as session:
            email = await get_ssllabs_email(session)
        self._client_settings_from(settings, email=email or "")

        async with self._ensure_task_lock():
            task = self._active_tasks.get(target_id)
            if task is not None:
                if task.done():
                    self._active_tasks.pop(target_id, None)
                else:
                    async with session_factory() as session:
                        active_scan = await ssllabs_repository.get_active_scan_for_target(
                            session,
                            target_id,
                            now=datetime.now(UTC),
                        )
                    if active_scan is None:
                        self._active_tasks.pop(target_id, None)
                    else:
                        return SslLabsScanRequestResult(
                            scan_id=active_scan.id,
                            host=active_scan.host,
                            created=False,
                            status=active_scan.status,
                        )

            session_factory = get_session_factory()
            async with session_factory() as session:
                row = await ssllabs_repository.get_target_with_site(session, target_id)
                if row is None:
                    raise SslLabsServiceError("SSL Labs target not found.")
                target, _site = row
                validate_ssllabs_host(target.host)

                now = datetime.now(UTC)
                active_scan = await ssllabs_repository.get_active_scan_for_target(session, target.id, now=now)
                if active_scan is not None:
                    return SslLabsScanRequestResult(
                        scan_id=active_scan.id,
                        host=active_scan.host,
                        created=False,
                        status=active_scan.status,
                    )

                if force_new and target.last_scan_started_at is not None:
                    seconds_since_last_start = (now - target.last_scan_started_at).total_seconds()
                    if seconds_since_last_start < _MIN_SECONDS_BETWEEN_NEW_SCANS:
                        remaining_seconds = int(_MIN_SECONDS_BETWEEN_NEW_SCANS - seconds_since_last_start)
                        raise SslLabsServiceError(
                            f"A new SSL Labs scan can be started again in {remaining_seconds} seconds."
                        )

                scan = await ssllabs_repository.create_scan_if_none_active(
                    session,
                    target,
                    status="queued",
                    now=now,
                )
                if scan is None:
                    active_scan = await ssllabs_repository.get_active_scan_for_target(session, target.id, now=now)
                    if active_scan is None:
                        raise SslLabsServiceError("SSL Labs scan state changed unexpectedly.")
                    return SslLabsScanRequestResult(
                        scan_id=active_scan.id,
                        host=active_scan.host,
                        created=False,
                        status=active_scan.status,
                    )
                if force_new:
                    target.last_scan_started_at = now
                await session.commit()

            task = asyncio.create_task(
                self._run_scan(target_id=target_id, scan_id=scan.id, force_new=force_new),
                name=f"ssllabs-scan-{target_id}",
            )
            try:
                self._active_tasks[target_id] = task
                task.add_done_callback(
                    lambda done_task, *, current_target_id=target_id: self._discard_scan_task(
                        current_target_id,
                        done_task,
                    )
                )
            except Exception:
                task.cancel()
                self._active_tasks.pop(target_id, None)
                raise
            return SslLabsScanRequestResult(scan_id=scan.id, host=scan.host, created=True, status=scan.status)

    def _discard_scan_task(self, target_id: int, task: asyncio.Task[None]) -> None:
        self._active_tasks.pop(target_id, None)

        if task.cancelled():
            return

        with suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "SSL Labs scan task crashed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                    extra={"target_id": target_id},
                )

    async def _publish_scan_event(
        self,
        *,
        target_id: int,
        scan_id: int,
        status: SslLabsScanStatus,
        payload: dict[str, Any] | None,
    ) -> None:
        await try_publish_resource_event(
            "ssllabs_scan",
            ssllabs_scan_event_action(status),
            str(target_id),
            {
                "scan_id": scan_id,
                "status": status,
                "grade": _extract_grade(payload or {}),
                "host": (payload or {}).get("host") or None,
            },
        )

    async def _mark_scan_state(
        self,
        *,
        target_id: int,
        scan_id: int,
        payload: dict[str, Any] | None,
        status: SslLabsScanStatus,
        completed: bool = False,
        next_poll_at: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        session_factory = get_session_factory()
        async with session_factory() as session:
            target = await ssllabs_repository.get_target_by_id(session, target_id)
            scan = await ssllabs_repository.get_scan_by_id(session, scan_id)
            if target is None or scan is None:
                return

            now = datetime.now(UTC)
            scan.status = status
            scan.grade = _extract_grade(payload or {})
            endpoints = (payload or {}).get("endpoints")
            scan.endpoint_count = len(endpoints) if isinstance(endpoints, list) else 0
            scan.result_json = payload
            scan.error_code = error_code
            scan.error_message = error_message or _extract_error_message(payload or {})
            scan.next_poll_at = next_poll_at
            if completed:
                scan.completed_at = now
                target.last_scan_completed_at = now
                if scan.grade is not None:
                    rank = grade_to_rank(scan.grade)
                    if rank is not None:
                        await ssllabs_repository.record_rank_history(
                            session,
                            host=scan.host,
                            grade=scan.grade,
                            rank=rank,
                            recorded_at=now,
                        )
                if target.schedule_frequency is None:
                    target.next_scheduled_at = None
                elif target.next_scheduled_at is None:
                    target.next_scheduled_at = _next_scheduled_at_for_target(target, now)
                elif target.next_scheduled_at <= now:
                    jitter_key = f"{getattr(target, 'site_id', 'unknown')}:{getattr(target, 'host', 'unknown')}"
                    target.next_scheduled_at = next_schedule_time(
                        target.schedule_frequency,
                        target.next_scheduled_at,
                        jitter_key=jitter_key,
                        max_jitter=_WEEKLY_SCHEDULE_JITTER,
                        minimum_after=now,
                        reference_includes_jitter=True,
                    )

            await session.commit()
        await self._publish_scan_event(target_id=target_id, scan_id=scan_id, status=status, payload=payload)

    async def _sleep_or_shutdown(self, seconds: int) -> None:
        try:
            await asyncio.wait_for(self._ensure_shutdown_event().wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
        raise asyncio.CancelledError

    async def _run_scan(self, *, target_id: int, scan_id: int, force_new: bool) -> None:
        settings = get_settings()
        host = "unknown"
        client: SslLabsClient | None = None

        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                row = await ssllabs_repository.get_target_with_site(session, target_id)
                if row is None:
                    await self._mark_scan_state(
                        target_id=target_id,
                        scan_id=scan_id,
                        payload={"host": host},
                        status="failed",
                        completed=True,
                        error_code="TargetNotFound",
                        error_message="SSL Labs target not found.",
                    )
                    return
                target, _site = row
                host = validate_ssllabs_host(target.host)
                email = await get_ssllabs_email(session)

            if not email:
                raise SslLabsServiceError("SSL Labs email is not configured.")
            client = SslLabsClient(self._client_settings_from(settings, email=email))

            await self._mark_scan_state(target_id=target_id, scan_id=scan_id, payload={"host": host}, status="queued")

            scan_started_at = datetime.now(UTC)
            start_new = force_new
            from_cache = not force_new
            max_age_hours = settings.ssllabs_cache_max_age_hours if from_cache else None
            while True:
                if (datetime.now(UTC) - scan_started_at).total_seconds() > _MAX_SCAN_DURATION_SECONDS:
                    raise SslLabsServiceError("SSL Labs scan exceeded maximum duration.")
                try:
                    payload = await client.analyze(
                        host=host,
                        start_new=start_new,
                        from_cache=from_cache,
                        max_age_hours=max_age_hours,
                    )
                except SslLabsRetryableError as exc:
                    retry_at = datetime.now(UTC) + timedelta(seconds=exc.retry_after_seconds)
                    await self._mark_scan_state(
                        target_id=target_id,
                        scan_id=scan_id,
                        payload={"host": host},
                        status="rate_limited",
                        next_poll_at=retry_at,
                        error_code=f"http_{exc.status_code}",
                        error_message=str(exc),
                    )
                    start_new = False
                    from_cache = False
                    max_age_hours = None
                    await self._sleep_or_shutdown(exc.retry_after_seconds)
                    continue
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    retry_seconds = _RUNNING_POLL_SECONDS * 3
                    retry_at = datetime.now(UTC) + timedelta(seconds=retry_seconds)
                    logger.warning("Temporary SSL Labs request failure for %s: %s", host, exc)
                    await self._mark_scan_state(
                        target_id=target_id,
                        scan_id=scan_id,
                        payload={"host": host},
                        status="rate_limited",
                        next_poll_at=retry_at,
                        error_code=exc.__class__.__name__,
                        error_message="SSL Labs request temporarily failed.",
                    )
                    start_new = False
                    from_cache = False
                    max_age_hours = None
                    await self._sleep_or_shutdown(retry_seconds)
                    continue

                start_new = False
                from_cache = False
                max_age_hours = None

                status = _map_remote_status(payload)
                if is_ssllabs_scan_terminal(status):
                    await self._mark_scan_state(
                        target_id=target_id,
                        scan_id=scan_id,
                        payload=payload,
                        status=status,
                        completed=True,
                    )
                    return

                poll_delay = _poll_delay_for_status(status)
                await self._mark_scan_state(
                    target_id=target_id,
                    scan_id=scan_id,
                    payload=payload,
                    status=status,
                    next_poll_at=datetime.now(UTC) + timedelta(seconds=poll_delay),
                )
                await self._sleep_or_shutdown(poll_delay)
        except asyncio.CancelledError:
            await self._mark_scan_state(
                target_id=target_id,
                scan_id=scan_id,
                payload={"host": host},
                status="failed",
                completed=False,
                error_code="cancelled",
                error_message="SSL Labs scan cancelled during shutdown.",
            )
            raise
        except SslLabsEmailNotRegisteredError:
            logger.warning("SSL Labs email is not registered for %s", host)
            await self._mark_scan_state(
                target_id=target_id,
                scan_id=scan_id,
                payload={"host": host},
                status="failed",
                completed=True,
                error_code="SslLabsEmailNotRegisteredError",
                error_message="SSL Labs email is not registered.",
            )
        except SslLabsClientError as exc:
            logger.warning("SSL Labs scan failed for %s: %s", host, exc)
            await self._mark_scan_state(
                target_id=target_id,
                scan_id=scan_id,
                payload={"host": host},
                status="failed",
                completed=True,
                error_code=exc.__class__.__name__,
                error_message=str(exc),
            )
        except ValueError:
            logger.warning("SSL Labs scan failed for %s: invalid target", host)
            await self._mark_scan_state(
                target_id=target_id,
                scan_id=scan_id,
                payload={"host": host},
                status="failed",
                completed=True,
                error_code="ValueError",
                error_message="Invalid SSL Labs target.",
            )
        except httpx.HTTPError:
            logger.warning("SSL Labs scan failed for %s: request failed", host)
            await self._mark_scan_state(
                target_id=target_id,
                scan_id=scan_id,
                payload={"host": host},
                status="failed",
                completed=True,
                error_code="HTTPError",
                error_message="SSL Labs request failed.",
            )
        except Exception as exc:
            logger.exception("Unexpected SSL Labs scan failure for %s", host)
            await self._mark_scan_state(
                target_id=target_id,
                scan_id=scan_id,
                payload={"host": host},
                status="failed",
                completed=True,
                error_code=exc.__class__.__name__,
                error_message="Unexpected SSL Labs scan failure.",
            )
        finally:
            if client is not None:
                await client.aclose()

    async def prune_rank_history(self, session: AsyncSession, *, now: datetime | None = None) -> int:
        """Delete rank-history samples beyond the configured retention window."""
        reference = now or datetime.now(UTC)
        retention_days = await get_ssllabs_history_retention_days(session)
        cutoff = reference - timedelta(days=retention_days)
        removed = await ssllabs_repository.prune_rank_history_older_than(session, cutoff=cutoff)
        if removed:
            logger.info("Pruned %d SSL Labs rank-history rows older than %d days.", removed, retention_days)
        return removed

    async def _scheduler_loop(self) -> None:
        while not self._ensure_shutdown_event().is_set():
            try:
                session_factory = get_session_factory()
                async with session_factory() as session:
                    await self.prune_rank_history(session)
                    await session.commit()
                    email = await get_ssllabs_email(session)
                    if not email:
                        await self._sleep_or_shutdown(_SCHEDULER_POLL_SECONDS)
                        continue
                    await self.sync_targets(session)
                    await session.commit()
                    due_targets = await ssllabs_repository.list_due_targets(session, now=datetime.now(UTC))

                for target in due_targets:
                    try:
                        await self.request_scan(target_id=target.id, force_new=True)
                    except (SslLabsServiceError, ValueError) as exc:
                        logger.info("Skipping scheduled SSL Labs scan for %s: %s", target.host, exc)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("SSL Labs scheduler iteration failed")

            await self._sleep_or_shutdown(_SCHEDULER_POLL_SECONDS)


ssllabs_service = SslLabsService()


def _as_utc(value: datetime) -> datetime:
    """Return *value* as UTC, treating naive datetimes as UTC rather than local time."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _week_bucket(value: datetime) -> str:
    """Return the ISO date (YYYY-MM-DD) of the Monday that starts the week containing *value*."""
    utc = _as_utc(value)
    monday = utc.date() - timedelta(days=utc.isoweekday() - 1)
    return monday.isoformat()


@dataclass(slots=True, frozen=True)
class SslLabsRankPoint:
    date: str  # ISO date (UTC) of the week's Monday
    grade: str
    rank: int


@dataclass(slots=True, frozen=True)
class SslLabsRankSeries:
    host: str
    points: list[SslLabsRankPoint]


@dataclass(slots=True, frozen=True)
class SslLabsRankHistory:
    range_key: str
    days: int
    series: list[SslLabsRankSeries]


def resolve_history_range(range_key: str | None) -> tuple[str, int]:
    """Return a validated ``(range_key, days)`` pair, falling back to the default."""
    key = (range_key or "").strip()
    if key not in SSLLABS_HISTORY_RANGES:
        key = SSLLABS_HISTORY_DEFAULT_RANGE
    return key, SSLLABS_HISTORY_RANGES[key]


async def build_rank_history(
    session: AsyncSession,
    *,
    range_key: str | None = None,
    now: datetime | None = None,
) -> SslLabsRankHistory:
    """Build the per-host weekly SSL Labs rank timeseries for the dashboard chart.

    Samples are bucketed to the Monday of their UTC week; when a host has multiple
    samples in the same week the latest one wins, giving a clean weekly-resolution series.
    """
    key, days = resolve_history_range(range_key)
    reference = now or datetime.now(UTC)
    since = reference - timedelta(days=days)

    entries = await ssllabs_repository.list_rank_history_since(session, since=since)

    # host -> {week_monday -> (recorded_at, grade)}; keep the latest sample per week.
    by_host: dict[str, dict[str, tuple[datetime, str]]] = {}
    for entry in entries:
        rank = grade_to_rank(entry.grade)
        if rank is None:
            continue
        week = _week_bucket(entry.recorded_at)
        week_map = by_host.setdefault(entry.host, {})
        existing = week_map.get(week)
        if existing is None or entry.recorded_at >= existing[0]:
            week_map[week] = (entry.recorded_at, entry.grade)

    series: list[SslLabsRankSeries] = []
    for host in sorted(by_host):
        points = [
            SslLabsRankPoint(date=week, grade=grade, rank=grade_to_rank(grade) or 0)
            for week, (_recorded_at, grade) in sorted(by_host[host].items())
        ]
        series.append(SslLabsRankSeries(host=host, points=points))

    return SslLabsRankHistory(range_key=key, days=days, series=series)
