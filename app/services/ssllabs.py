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

import httpx

from app.config.settings import Settings, get_settings
from app.database.session import get_session_factory
from app.repositories.sites import site_repository
from app.repositories.ssllabs import TERMINAL_SCAN_STATUSES, ssllabs_repository
from app.schemas.ssllabs import SslLabsScanStatus, SslLabsScheduleFrequency
from app.services.events import publish_resource_event
from app.services.runtime_settings import get_ssllabs_email
from app.utils.ssllabs import mask_email, next_schedule_time, validate_ssllabs_host


logger = logging.getLogger(__name__)

_INITIAL_POLL_SECONDS = 5
_RUNNING_POLL_SECONDS = 10
_SCHEDULER_POLL_SECONDS = 60
_MIN_SECONDS_BETWEEN_NEW_SCANS = 300


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


def _retry_after_seconds(response: httpx.Response, fallback: int) -> int:
    raw_value = response.headers.get("Retry-After")
    if raw_value is None:
        return fallback
    try:
        parsed = int(raw_value)
    except ValueError:
        return fallback
    return max(parsed, fallback)


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


# Registration status cache: {email: (is_registered, timestamp)}
_registration_status_cache: dict[str, tuple[bool, datetime]] = {}
_REGISTRATION_CACHE_TTL_SECONDS = 300  # 5 minutes


async def check_email_registration_status(
    email: str,
    *,
    api_base_url: str = "https://api.ssllabs.com/api/v4",
    use_cache: bool = True,
) -> bool:
    """
    Check if an email is registered with SSL Labs API v4.

    Uses a TTL cache to avoid excessive API calls.
    Returns True if registered, False otherwise.
    """
    now = datetime.now(UTC)

    # Check cache first
    if use_cache and email in _registration_status_cache:
        is_registered, cached_at = _registration_status_cache[email]
        age_seconds = (now - cached_at).total_seconds()
        if age_seconds < _REGISTRATION_CACHE_TTL_SECONDS:
            return is_registered

    # Make a lightweight API call to check registration
    # Using the info endpoint or a minimal analyze request
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            # Try to call the info endpoint with the email header
            response = await client.get(
                f"{api_base_url.rstrip('/')}/info",
                headers={"email": email},
            )
            if response.status_code == 200:
                _registration_status_cache[email] = (True, now)
                return True
            if response.status_code == 400:
                body = response.text.lower()
                if "not yet registered" in body or "register api" in body:
                    _registration_status_cache[email] = (False, now)
                    return False
            # Other errors - assume not registered
            _registration_status_cache[email] = (False, now)
            return False
    except Exception as exc:
        logger.warning("Failed to check SSL Labs registration status: %s", exc)
        return False


def clear_registration_status_cache(email: str | None = None) -> None:
    """Clear the registration status cache for a specific email or all emails."""
    if email is None:
        _registration_status_cache.clear()
    else:
        _registration_status_cache.pop(email, None)


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
    # Extract name parts from email (before @)
    local_part = email.split("@")[0] if "@" in email else "User"
    # Simple heuristic: split on . or _ or treat as single name
    name_parts = local_part.replace("_", ".").split(".")
    first_name = name_parts[0].title() if name_parts else "User"
    last_name = name_parts[1].title() if len(name_parts) > 1 else "User"

    payload = {
        "firstName": first_name,
        "lastName": last_name,
        "email": email,
        "organization": organization,
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                f"{api_base_url.rstrip('/')}/register",
                json=payload,
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    logger.info("Successfully registered email %s with SSL Labs", mask_email(email))
                    # Clear cache to reflect new registration status
                    clear_registration_status_cache(email)
                    return True
            logger.warning(
                "SSL Labs registration failed: HTTP %d - %s",
                response.status_code,
                response.text[:200],
            )
            return False
    except Exception as exc:
        logger.warning("SSL Labs registration error: %s", exc)
        return False


class SslLabsClient:
    def __init__(
        self,
        settings: SslLabsClientSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        masked = mask_email(settings.email)
        logger.debug("Initializing SSL Labs client with email: %s", masked)
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.api_base_url.rstrip("/") + "/",
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
            # Log response body for debugging
            try:
                body = response.text[:500]
            except Exception:
                body = "<unreadable>"
            logger.warning("SSL Labs HTTP 400 response body: %s", body)
            # Check if it's a "not registered" error
            if "not yet registered" in body.lower() or "register api" in body.lower():
                raise SslLabsEmailNotRegisteredError(
                    "SSL Labs email is not yet registered. Registration required."
                )
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
        self._client: SslLabsClient | None = None
        self._task_lock: asyncio.Lock | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._scheduler_task: asyncio.Task[None] | None = None
        self._active_tasks: dict[int, asyncio.Task[None]] = {}
        self._registration_attempted: bool = False

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
            api_base_url=settings.ssllabs_api_base_url,
            email=email,
            timeout_seconds=settings.ssllabs_timeout_seconds,
        )

    async def masked_email(self) -> str | None:
        session_factory = get_session_factory()
        async with session_factory() as session:
            return mask_email(await get_ssllabs_email(session))

    async def _get_client(self, settings: Settings, *, email: str) -> SslLabsClient:
        client_settings = self._client_settings_from(settings, email=email)
        if self._client is None:
            self._client = SslLabsClient(client_settings)
        elif self._client.settings != client_settings:
            await self._client.aclose()
            self._client = SslLabsClient(client_settings)
        return self._client

    async def startup(self, settings: Settings | None = None) -> None:
        effective_settings = settings or get_settings()
        shutdown_event = self._ensure_shutdown_event()
        shutdown_event.clear()
        if self._scheduler_task is not None:
            return
        session_factory = get_session_factory()
        async with session_factory() as session:
            email = await get_ssllabs_email(session)
        if not email:
            logger.info("SSL Labs scheduler is idle until ssllabs_email is configured.")
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

        if self._client is not None:
            await self._client.aclose()
            self._client = None

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
            target.next_scheduled_at = (
                next_schedule_time(frequency, datetime.now(UTC)) if frequency is not None else None
            )
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
            if target_id in self._active_tasks:
                async with session_factory() as session:
                    active_scan = await ssllabs_repository.get_active_scan_for_target(
                        session,
                        target_id,
                        now=datetime.now(UTC),
                    )
                    if active_scan is None:
                        raise SslLabsServiceError("SSL Labs scan task state is inconsistent.")
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
            self._active_tasks[target_id] = task
            task.add_done_callback(lambda _task, *, current_target_id=target_id: self._active_tasks.pop(current_target_id, None))
            return SslLabsScanRequestResult(scan_id=scan.id, host=scan.host, created=True, status=scan.status)

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
                if target.schedule_frequency is not None:
                    target.next_scheduled_at = next_schedule_time(target.schedule_frequency, now)

            await session.commit()

        action = "scan_updated"
        if status == "queued":
            action = "scan_started"
        elif status in TERMINAL_SCAN_STATUSES:
            action = "scan_completed" if status == "ready" else "scan_failed"
        await publish_resource_event(
            "ssllabs_scan",
            action,
            str(target_id),
            {"scan_id": scan_id, "status": status, "grade": _extract_grade(payload or {}), "host": (payload or {}).get("host") or None},
        )

    async def _sleep_or_shutdown(self, seconds: int) -> None:
        try:
            await asyncio.wait_for(self._ensure_shutdown_event().wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return
        raise asyncio.CancelledError

    async def _run_scan(self, *, target_id: int, scan_id: int, force_new: bool) -> None:
        settings = get_settings()
        session_factory = get_session_factory()
        async with session_factory() as session:
            row = await ssllabs_repository.get_target_with_site(session, target_id)
            if row is None:
                return
            target, _site = row
            host = validate_ssllabs_host(target.host)
            email = await get_ssllabs_email(session)

        if not email:
            raise SslLabsServiceError("SSL Labs email is not configured.")

        await self._mark_scan_state(target_id=target_id, scan_id=scan_id, payload={"host": host}, status="queued")

        client = await self._get_client(settings, email=email)
        start_new = force_new
        from_cache = not force_new
        max_age_hours = settings.ssllabs_cache_max_age_hours if from_cache else None

        try:
            while True:
                try:
                    payload = await client.analyze(
                        host=host,
                        start_new=start_new,
                        from_cache=from_cache,
                        max_age_hours=max_age_hours,
                    )
                except SslLabsEmailNotRegisteredError:
                    # Auto-register if not yet attempted
                    if not self._registration_attempted:
                        self._registration_attempted = True
                        if email and await register_email_with_ssllabs(
                            email,
                            api_base_url=settings.ssllabs_api_base_url,
                        ):
                            # Registration succeeded, retry the request
                            logger.info("Auto-registered with SSL Labs, retrying scan...")
                            continue
                    # Registration failed or already attempted
                    raise SslLabsClientError(
                        "SSL Labs email registration failed. Please register manually."
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

                start_new = False
                from_cache = False
                max_age_hours = None

                status = _map_remote_status(payload)
                if status in TERMINAL_SCAN_STATUSES:
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
                completed=True,
                error_code="cancelled",
                error_message="SSL Labs scan cancelled during shutdown.",
            )
            raise
        except (SslLabsClientError, SslLabsServiceError, ValueError, httpx.HTTPError) as exc:
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

    async def _scheduler_loop(self) -> None:
        while not self._ensure_shutdown_event().is_set():
            try:
                session_factory = get_session_factory()
                async with session_factory() as session:
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