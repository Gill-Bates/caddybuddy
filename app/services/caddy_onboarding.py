#!/usr/bin/env python3
#
# app/services/caddy_onboarding.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""First-run Caddy onboarding wizard service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.models.entities import CaddyBuddyState
from app.services.caddy import CaddyAdminClient, CaddyServiceError
from app.services.caddyfile_manager import onboard_caddy, onboarding_succeeded
from app.services.supervisor import DisabledSupervisor, get_caddy_supervisor
from app.utils.caddyfile import inject_global_options, parse_caddyfile
from app.services.runtime_settings import (
    get_caddy_config,
    get_ssllabs_email,
    discover_caddyfile_candidates,
    normalize_caddy_api_url,
    normalize_caddyfile_path,
    normalize_ssllabs_email,
    suggest_caddyfile_path,
    set_caddy_api_url,
    set_caddyfile_path,
    set_ssllabs_email,
)


logger = logging.getLogger(__name__)
_DEFAULT_CADDYFILE_PATH = Path("/opt/caddybuddy/Caddyfile")

# Modes whose running Caddy can have its disabled Admin API enabled in place.
_ADMIN_API_ASSIST_MODES: frozenset[str] = frozenset({"host", "existing_config"})
# Poll budget while waiting for the Admin API to come up after a restart.
_ADMIN_API_ENABLE_POLL_ATTEMPTS = 10
_ADMIN_API_ENABLE_POLL_SECONDS = 1.5

OnboardingMode = Literal[
    "host",
    "docker",
    "missing",
    "existing_config",
    "unconfigured",
    "default_config",
]
OnboardingRuntimeLocation = Literal["host", "container"]
OnboardingStatus = Literal["not_started", "in_progress", "failed", "completed"]

_STATE_KEY = "caddy_onboarding_wizard"
_VALID_MODES: set[str] = {
    "host",
    "docker",
    "missing",
    "existing_config",
    "unconfigured",
    "default_config",
}
_VALID_RUNTIME_LOCATIONS: set[str] = {"host", "container"}
_MODE_LABELS = {
    "host": "Caddy runs on this host",
    "docker": "Caddy runs in another Docker container",
    "missing": "Caddy is not installed yet",
    "existing_config": "Caddy already has an active config to import",
    "unconfigured": "Caddy is installed but not configured",
    "default_config": "Deploy starter config (replaces existing)",
}
_RUNTIME_LOCATION_LABELS = {
    "host": "Host",
    "container": "Docker container",
}

# Two-question step-1 model. The wizard asks "Where does Caddy run?" and (only for the
# host path) "What should CaddyBuddy start from?". Both answers map back to the single
# OnboardingMode the preflight/execute state machine already understands, so the wizard's
# decision flow stays linear without changing that state machine.
_CADDY_LOCATIONS: tuple[tuple[str, str, str], ...] = (
    ("host", "Caddy runs on this host",
     "CaddyBuddy and Caddy share the same Linux host."),
    ("docker", "Caddy runs in a Docker container",
     "CaddyBuddy reaches Caddy through a container network or host.docker.internal."),
    ("missing", "Caddy is not installed yet",
     "CaddyBuddy stores the settings and finishes once Caddy is reachable."),
)
_CADDY_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("existing", "Import its existing active config",
     "Back up and take over the configuration Caddy already runs."),
    ("empty", "Start from an empty Caddy",
     "CaddyBuddy deploys its bundled starter Caddyfile. Any existing active config is backed up and replaced."),
)
_CADDY_SOURCE_TO_MODE: dict[str, str] = {
    "existing": "existing_config",
    "empty": "default_config",
}
# Reverse map so returning to step 1 can re-check the radios that produced the stored mode.
# ``unconfigured`` and ``default_config`` behave identically (both deploy the bundled config),
# so both map back to the single "empty" source choice.
_MODE_TO_CHOICE: dict[str, tuple[str, str]] = {
    "missing": ("missing", ""),
    "docker": ("docker", ""),
    "existing_config": ("host", "existing"),
    "unconfigured": ("host", "empty"),
    "default_config": ("host", "empty"),
    "host": ("host", "existing"),
}


@dataclass(slots=True)
class OnboardingWizardState:
    status: OnboardingStatus = "not_started"
    mode: str | None = None
    runtime_location: str = ""
    admin_api_url: str = "http://localhost:2019"
    caddy_version: str | None = None
    caddyfile_path: str = ""
    acme_email: str = ""
    backup_path: str | None = None
    last_preflight_at: str | None = None
    preflight_passed: bool = False
    completed_at: str | None = None
    error_message: str | None = None
    exclusive_manager_confirmed: bool = False
    api_only_takeover: bool = False
    pending_location: str = ""
    preflight_errors: list[str] = field(default_factory=list)
    preflight_warnings: list[str] = field(default_factory=list)
    field_errors: dict[str, list[str]] = field(default_factory=dict)
    field_check_statuses: dict[str, str] = field(default_factory=dict)
    field_check_values: dict[str, str] = field(default_factory=dict)
    admin_api_reachable: bool = False
    admin_config_readable: bool = False
    caddyfile_readable: bool = False
    caddyfile_writable: bool = False
    default_config_exists: bool = False
    # UI-only: drives the step-2 "Enable Admin API" assist panel. NEVER trusted by the service
    # action, which recomputes every safety condition before touching the Caddyfile.
    admin_api_assist_available: bool = False

    @property
    def mode_label(self) -> str:
        if self.mode is None:
            return "Not selected"
        return _MODE_LABELS.get(self.mode, self.mode)

    @property
    def runtime_location_label(self) -> str:
        if not self.runtime_location:
            return "Not selected"
        if self.runtime_location == "docker":
            return _RUNTIME_LOCATION_LABELS["container"]
        return _RUNTIME_LOCATION_LABELS.get(self.runtime_location, self.runtime_location)

    @property
    def preflight_ok(self) -> bool:
        return self.status == "in_progress" and bool(self.last_preflight_at) and not self.preflight_errors

    @classmethod
    def from_json(cls, raw: str | None) -> OnboardingWizardState:
        if not raw:
            return cls()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid onboarding wizard state JSON.")
            return cls()
        if not isinstance(payload, dict):
            return cls()
        allowed = {field_name for field_name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in payload.items() if key in allowed})

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))


def onboarding_modes() -> list[dict[str, str]]:
    return [
        {"value": value, "label": label}
        for value, label in _MODE_LABELS.items()
    ]


def onboarding_caddy_locations() -> list[dict[str, str]]:
    return [
        {"value": value, "label": label, "description": description}
        for value, label, description in _CADDY_LOCATIONS
    ]


def onboarding_caddy_sources() -> list[dict[str, str]]:
    return [
        {"value": value, "label": label, "description": description}
        for value, label, description in _CADDY_SOURCES
    ]


def mode_to_choice(mode: str | None) -> tuple[str, str]:
    """Return the ``(location, source)`` radio selection that maps to ``mode``."""
    if not mode:
        return ("", "")
    return _MODE_TO_CHOICE.get(mode, ("", ""))


def derive_onboarding_mode(location: str, source: str) -> str:
    """Translate the two-question step-1 answers into a single ``OnboardingMode``.

    ``missing`` and ``docker`` ignore the source answer; ``host`` requires one of the
    supported source values. Any other input raises ``ValueError`` so the route surfaces a
    clear flash instead of persisting an invalid mode.
    """
    normalized_location = (location or "").strip().lower()
    if normalized_location == "missing":
        return "missing"
    if normalized_location == "docker":
        return "docker"
    if normalized_location == "host":
        mode = _CADDY_SOURCE_TO_MODE.get((source or "").strip().lower())
        if mode is None:
            raise ValueError("Choose what CaddyBuddy should start from before continuing.")
        return mode
    raise ValueError("Choose where Caddy runs before continuing.")


def onboarding_runtime_locations() -> list[dict[str, str]]:
    return [
        {"value": value, "label": label}
        for value, label in _RUNTIME_LOCATION_LABELS.items()
    ]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_caddyfile_path() -> Path:
    return _DEFAULT_CADDYFILE_PATH


def _is_loopback_host(hostname: str) -> bool:
    normalized = hostname.strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def normalize_runtime_location(raw_value: str) -> str:
    normalized = raw_value.strip().lower()
    if not normalized:
        raise ValueError("Choose where CaddyBuddy runs before continuing.")
    if normalized == "docker":
        return "container"
    if normalized not in _VALID_RUNTIME_LOCATIONS:
        raise ValueError("Choose a supported CaddyBuddy runtime location.")
    return normalized


def detect_runtime_location() -> str:
    dockerenv_markers = (Path("/.dockerenv"), Path("/run/.containerenv"))
    if any(marker.exists() for marker in dockerenv_markers):
        return "container"
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8").lower()
    except OSError:
        return "host"
    if any(token in cgroup for token in ("docker", "containerd", "kubepods", "podman", "lxc")):
        return "container"
    return "host"


def _default_config_modes() -> set[str]:
    return {"unconfigured", "default_config"}


def _requires_writable_caddyfile(mode: str | None) -> bool:
    return mode in {"host", "existing_config", "unconfigured", "default_config"}


def _reset_preflight_state(state: OnboardingWizardState) -> None:
    """Clear cached preflight/runtime facts before starting a new onboarding attempt."""
    state.last_preflight_at = None
    state.preflight_passed = False
    state.completed_at = None
    state.error_message = None
    state.exclusive_manager_confirmed = False
    state.api_only_takeover = False
    state.preflight_errors = []
    state.preflight_warnings = []
    state.field_errors = {}
    state.field_check_statuses = {}
    state.field_check_values = {}
    state.admin_api_reachable = False
    state.admin_config_readable = False
    state.caddy_version = None
    state.backup_path = None
    state.caddyfile_readable = False
    state.caddyfile_writable = False
    state.default_config_exists = False
    state.admin_api_assist_available = False


async def get_onboarding_state(session: AsyncSession) -> OnboardingWizardState:
    row = await session.get(CaddyBuddyState, _STATE_KEY)
    return OnboardingWizardState.from_json(row.value if row is not None else None)


async def lock_onboarding_state(session: AsyncSession) -> OnboardingWizardState:
    bind = session.get_bind()
    if bind.dialect.name == "sqlite" and not session.in_transaction():
        await session.execute(text("BEGIN IMMEDIATE"))
    row = await session.get(CaddyBuddyState, _STATE_KEY, with_for_update=True)
    return OnboardingWizardState.from_json(row.value if row is not None else None)


async def save_onboarding_state(session: AsyncSession, state: OnboardingWizardState) -> None:
    row = await session.get(CaddyBuddyState, _STATE_KEY)
    if row is None:
        session.add(CaddyBuddyState(key=_STATE_KEY, value=state.to_json()))
    else:
        row.value = state.to_json()
    await session.flush()


async def reset_onboarding_state(session: AsyncSession) -> OnboardingWizardState:
    """Reset the onboarding wizard to its initial not-started state."""
    await lock_onboarding_state(session)
    state = OnboardingWizardState()
    await save_onboarding_state(session, state)
    return state


async def start_onboarding(
    session: AsyncSession,
    *,
    mode: str,
    runtime_location: str | None = None,
) -> OnboardingWizardState:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in _VALID_MODES:
        raise ValueError("Choose a supported Caddy onboarding situation.")
    # The runtime location is auto-detected. An explicit, non-empty value is honoured
    # (and validated); a missing or blank value falls back to detection instead of
    # raising, so the wizard never blocks step 1 on a question it no longer asks.
    normalized_runtime_location = (
        normalize_runtime_location(runtime_location)
        if runtime_location and runtime_location.strip()
        else detect_runtime_location()
    )

    state = await lock_onboarding_state(session)
    if state.status == "completed":
        raise ValueError("Caddy onboarding is already completed.")
    _reset_preflight_state(state)
    state.status = "in_progress"
    state.mode = normalized_mode
    state.runtime_location = normalized_runtime_location
    state.caddyfile_path = suggest_caddyfile_path(
        normalized_runtime_location,
        mounted_caddyfile_path=get_settings().mounted_caddyfile_path,
    )
    await save_onboarding_state(session, state)
    return state


async def save_onboarding_location(
    session: AsyncSession,
    *,
    caddy_location: str,
) -> OnboardingWizardState:
    """Persist the step-1 location choice without deriving a mode yet.

    Sets ``pending_location`` so that step-2 can show the right follow-up
    (source cards for host, informational for docker/missing). Resets mode
    and all preflight state so the user can change their mind cleanly.
    """
    normalized = (caddy_location or "").strip().lower()
    if normalized not in {"host", "docker", "missing"}:
        raise ValueError("Choose where Caddy runs before continuing.")
    state = await lock_onboarding_state(session)
    if state.status == "completed":
        raise ValueError("Caddy onboarding is already completed.")
    _reset_preflight_state(state)
    state.mode = None
    state.status = "not_started"
    state.pending_location = normalized
    runtime_location = (
        "container" if normalized == "docker"
        else detect_runtime_location() if normalized == "missing"
        else "host"
    )
    state.runtime_location = runtime_location
    state.caddyfile_path = suggest_caddyfile_path(
        runtime_location,
        mounted_caddyfile_path=get_settings().mounted_caddyfile_path,
    )
    await save_onboarding_state(session, state)
    return state


def get_onboarding_caddyfile_path_candidates(
    runtime_location: str | None,
) -> tuple[str, ...]:
    return tuple(
        str(path)
        for path in discover_caddyfile_candidates(
            runtime_location or detect_runtime_location(),
            mounted_caddyfile_path=get_settings().mounted_caddyfile_path,
        )
    )


def _inspect_caddyfile_path(path_value: str, *, allow_create: bool = False) -> tuple[bool, bool, str | None]:
    try:
        normalized = normalize_caddyfile_path(path_value)
    except ValueError as exc:
        return False, False, str(exc)

    path = Path(normalized)
    if not path.exists():
        if allow_create:
            parent = path.parent
            if not parent.exists():
                return False, False, "Caddyfile parent directory does not exist."
            if not parent.is_dir():
                return False, False, "Caddyfile parent path is not a directory."
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=parent,
                    prefix=".caddybuddy-preflight-",
                    delete=True,
                ):
                    pass
            except OSError:
                return False, False, "Caddyfile parent directory is not writable."
            return False, True, None
        return False, False, "Caddyfile path does not exist yet."
    if not path.is_file():
        return False, False, "Caddyfile path is not a regular file."
    try:
        path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, False, "Caddyfile path is not readable."
    try:
        with path.open("r+", encoding="utf-8"):
            pass
    except OSError:
        return True, False, "Caddyfile path is readable but not writable."
    return True, True, None


def _caddyfile_atomically_replaceable_sync(path_value: str) -> tuple[bool, str | None]:
    """Return whether the Caddyfile can be replaced via temp-write + atomic ``replace``.

    Editing the Caddyfile in place writes a sibling temp file and ``os.replace``s it, which
    needs a writable *parent directory* on top of a writable file. ``_inspect_caddyfile_path``
    only checks parent writability when the file is missing, so this guards the existing-file case.
    """
    try:
        normalized = normalize_caddyfile_path(path_value)
    except ValueError as exc:
        return False, str(exc)

    parent = Path(normalized).parent
    if not parent.exists():
        return False, "Caddyfile parent directory does not exist."
    if not parent.is_dir():
        return False, "Caddyfile parent path is not a directory."
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=".caddybuddy-enable-",
            delete=True,
        ):
            pass
    except OSError:
        return False, "Caddyfile parent directory is not writable."
    return True, None


def _extract_global_email(content: str) -> str | None:
    """Return the ``email`` directive value from the Caddyfile global options block, if any."""
    block = parse_caddyfile(content).global_block
    for line in block.splitlines():
        match = re.match(r"^\s*email\s+(\S+)", line)
        if match:
            return match.group(1)
    return None


def _atomic_write_text(
    target_path: Path,
    content: str,
    *,
    mode: int | None = None,
    owner: tuple[int, int] | None = None,
) -> None:
    fd, raw_temp_path = tempfile.mkstemp(
        dir=target_path.parent,
        prefix=f".{target_path.name}.caddybuddy-",
        text=True,
    )
    temp_path = Path(raw_temp_path)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        if mode is not None:
            os.chmod(temp_path, mode)

        if owner is not None:
            with suppress(PermissionError, OSError):
                os.chown(temp_path, owner[0], owner[1])

        temp_path.replace(target_path)
    except Exception:
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)
        raise


def _enable_admin_in_caddyfile_sync(path_value: str, admin_endpoint: str) -> str:
    """Inject a single managed ``admin <endpoint>`` into the on-disk Caddyfile.

    Returns the backup path. The original file mode is preserved on the replacement; ownership is
    left untouched (best-effort). ``inject_global_options`` strips every existing ``admin …`` line
    in the global block (including ``admin off``) before adding exactly one managed directive. The
    user's existing ``email`` directive is preserved verbatim — onboarding must not silently drop
    the ACME contact email of a running Caddy when it merely enables the Admin API.
    """
    normalized = normalize_caddyfile_path(path_value)
    target_path = Path(normalized)
    if not target_path.is_file():
        raise ValueError("Caddyfile path is not a regular file.")
    try:
        original_content = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("Caddyfile path is not readable.") from exc

    existing_email = _extract_global_email(original_content)
    new_content = inject_global_options(original_content, admin=admin_endpoint, email=existing_email)
    if not new_content.endswith("\n"):
        new_content += "\n"

    backup_path = _build_unique_backup_path(target_path)
    try:
        with backup_path.open("x", encoding="utf-8") as backup_file:
            backup_file.write(original_content)
    except FileExistsError as exc:
        raise ValueError("Caddyfile backup already exists.") from exc
    except OSError as exc:
        raise ValueError("Could not write Caddyfile backup before enabling the Admin API.") from exc

    try:
        stat_result = target_path.stat()
        _atomic_write_text(
            target_path,
            new_content,
            mode=stat_result.st_mode & 0o777,
            owner=(stat_result.st_uid, stat_result.st_gid),
        )
    except OSError as exc:
        raise ValueError("Could not write the Admin API directive into the Caddyfile.") from exc

    return str(backup_path)


def _restore_caddyfile_sync(path_value: str, backup_path_value: str) -> None:
    """Restore the Caddyfile from a backup written by ``_enable_admin_in_caddyfile_sync``."""
    target_path = Path(normalize_caddyfile_path(path_value))
    backup_path = Path(backup_path_value)
    if not backup_path.is_file():
        raise ValueError("Caddyfile backup is missing; cannot restore the original configuration.")

    original_content = backup_path.read_text(encoding="utf-8")
    # Preserve the live file's current mode (which equals the original — the enable step kept it);
    # fall back to the backup's mode only when the target has gone missing.
    try:
        target_stat = target_path.stat()
    except OSError:
        target_stat = backup_path.stat()
    try:
        _atomic_write_text(
            target_path,
            original_content,
            mode=target_stat.st_mode & 0o777,
            owner=(target_stat.st_uid, target_stat.st_gid),
        )
    except OSError as exc:
        raise ValueError("Could not restore the original Caddyfile from backup.") from exc


def _read_default_config_sync() -> str:
    path = _default_caddyfile_path()
    if not path.is_file():
        raise ValueError("Default config /opt/caddybuddy/Caddyfile does not exist.")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("Default config /opt/caddybuddy/Caddyfile is not readable.") from exc


def _render_default_config(content: str, *, acme_email: str, admin_api_url: str) -> str:
    """Substitute known placeholders and reject any that remain unresolved."""
    rendered = content
    if acme_email:
        rendered = rendered.replace("{{ ACME_EMAIL }}", acme_email)
    if admin_api_url:
        rendered = rendered.replace("{{ CADDY_ADMIN_API_URL }}", admin_api_url)
    if "{{" in rendered:
        raise ValueError("Default Caddyfile contains unresolved placeholders.")
    return rendered


def _build_unique_backup_path(target_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return target_path.with_name(f"{target_path.name}.caddybuddy-backup-{timestamp}")


def _prepare_default_config_sync(
    target_path_value: str,
    *,
    acme_email: str = "",
    admin_api_url: str = "",
) -> str | None:
    raw = _read_default_config_sync()
    content = _render_default_config(raw, acme_email=acme_email, admin_api_url=admin_api_url)
    try:
        target_path = Path(normalize_caddyfile_path(target_path_value))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    backup_path: Path | None = None
    mode: int | None = 0o600
    owner: tuple[int, int] | None = None
    target_existed = target_path.exists()
    if target_existed:
        if not target_path.is_file():
            raise ValueError("Caddyfile path is not a regular file.")
        try:
            original_content = target_path.read_text(encoding="utf-8")
            stat_result = target_path.stat()
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError("Caddyfile path is not readable.") from exc
        mode = stat_result.st_mode & 0o777
        owner = (stat_result.st_uid, stat_result.st_gid)
        backup_path = _build_unique_backup_path(target_path)
        try:
            with backup_path.open("x", encoding="utf-8") as backup_file:
                backup_file.write(original_content)
        except FileExistsError as exc:
            raise ValueError("Caddyfile backup already exists.") from exc
        except OSError as exc:
            raise ValueError("Could not write Caddyfile backup before applying the default config.") from exc

    try:
        _atomic_write_text(
            target_path,
            content,
            mode=mode,
            owner=owner,
        )
    except OSError as exc:
        raise ValueError("Could not prepare the default Caddyfile for onboarding.") from exc

    return str(backup_path) if backup_path is not None else None


def _rollback_prepared_default_config_sync(target_path_value: str, backup_path_value: str | None) -> None:
    target_path = Path(normalize_caddyfile_path(target_path_value))

    if backup_path_value is None:
        if target_path.exists():
            target_path.unlink()
        return

    backup_path = Path(backup_path_value)
    if not backup_path.is_file():
        raise ValueError("Caddyfile backup is missing; cannot restore failed onboarding.")

    try:
        preserve_stat = target_path.stat()
    except OSError:
        preserve_stat = backup_path.stat()
    _atomic_write_text(
        target_path,
        backup_path.read_text(encoding="utf-8"),
        mode=preserve_stat.st_mode & 0o777,
        owner=(preserve_stat.st_uid, preserve_stat.st_gid),
    )


async def _mark_onboarding_failed(
    session: AsyncSession,
    state: OnboardingWizardState,
    message: str,
    *,
    exclusive_manager_confirmed: bool,
) -> OnboardingWizardState:
    state.status = "failed"
    state.completed_at = None
    state.error_message = message
    state.preflight_errors = [message]
    state.field_errors = {}
    state.exclusive_manager_confirmed = exclusive_manager_confirmed
    await save_onboarding_state(session, state)
    return state


async def _restore_previous_runtime_settings(
    session: AsyncSession,
    *,
    previous_admin_api_url: str,
    previous_caddyfile_path: str,
    previous_email: str | None,
) -> None:
    await set_caddy_api_url(session, previous_admin_api_url)
    await set_caddyfile_path(session, previous_caddyfile_path)
    await set_ssllabs_email(session, previous_email or "")
    await session.flush()


async def _probe_admin_api(admin_api_url: str) -> tuple[bool, str | None, bool, str | None]:
    settings = get_settings()
    try:
        async with CaddyAdminClient(admin_api_url, settings.caddy_admin_timeout_seconds) as client:
            reachable = await client.health()
            version = await client.get_version() if reachable else None
            config_readable = False
            if reachable:
                try:
                    await client.get_config()
                    config_readable = True
                except CaddyServiceError:
                    config_readable = False
            return reachable, version, config_readable, None
    except (CaddyServiceError, OSError, ValueError) as exc:
        return False, None, False, str(exc)


async def run_onboarding_preflight(
    session: AsyncSession,
    *,
    admin_api_url: str,
    acme_email: str,
    caddyfile_path: str,
) -> OnboardingWizardState:
    state = await lock_onboarding_state(session)
    if state.mode is None:
        raise ValueError("Choose an onboarding situation before running preflight.")

    errors: list[str] = []
    warnings: list[str] = []
    field_errors: dict[str, list[str]] = {}
    default_config_exists = _default_caddyfile_path().is_file()

    def add_error(message: str, field_name: str | None = None) -> None:
        errors.append(message)
        if field_name is not None:
            field_errors.setdefault(field_name, []).append(message)

    try:
        normalized_admin_url = normalize_caddy_api_url(admin_api_url)
        admin_url_valid = True
    except ValueError as exc:
        normalized_admin_url = admin_api_url.strip()
        admin_url_valid = False
        add_error(str(exc), "admin_api_url")

    normalized_email = ""
    if acme_email.strip():
        try:
            normalized_email = normalize_ssllabs_email(acme_email)
        except ValueError as exc:
            add_error(str(exc), "acme_email")
    else:
        add_error("ACME/TLS email is required.", "acme_email")

    try:
        normalized_caddyfile_path = normalize_caddyfile_path(caddyfile_path)
    except ValueError as exc:
        normalized_caddyfile_path = caddyfile_path.strip()
        add_error(str(exc), "caddyfile_path")

    reachable = False
    version = None
    config_readable = False
    if admin_url_valid:
        reachable, version, config_readable, api_error = await _probe_admin_api(normalized_admin_url)
        if api_error:
            warnings.append(api_error)
    if state.mode == "missing" and not reachable:
        warnings.append("Caddy is not installed yet, so the Admin API and version check are skipped.")
    elif not reachable:
        add_error(
            "Caddy Admin API is not reachable from CaddyBuddy. CaddyBuddy manages Caddy exclusively "
            "through this API, so it must be running and reachable. A freshly installed Caddy often has "
            "the admin endpoint disabled — enable it (it listens on localhost:2019 by default unless "
            "'admin off' is set) and re-run preflight.",
            "admin_api_url",
        )
    # Caddy's admin API has no version endpoint (GET / returns 404), so an absent version
    # string is normal and must not block onboarding. A reachable structured admin API —
    # /config/ returning JSON, which is what `reachable` confirms — is itself proof of Caddy
    # 2.x, since the JSON config API does not exist in Caddy 1.x. Only reject a version that
    # is actually reported and is not 2.x.
    if version is not None and not version.lstrip("v").startswith("2."):
        add_error(f"Caddy 2.x is required; detected {version}.", "admin_api_url")
    if state.mode == "existing_config" and not config_readable:
        add_error(
            "Existing Caddy config must be readable through the Admin API before takeover.",
            "admin_api_url",
        )

    allow_target_create = state.mode in _default_config_modes()
    caddyfile_readable, caddyfile_writable, caddyfile_error = await asyncio.to_thread(
        _inspect_caddyfile_path,
        normalized_caddyfile_path,
        allow_create=allow_target_create,
    )
    docker_api_only = state.mode == "docker" and bool(caddyfile_error)
    if caddyfile_error:
        if state.mode == "docker":
            warnings.append(
                f"{caddyfile_error} Docker onboarding can continue only if Admin API takeover is sufficient."
            )
        elif state.mode == "existing_config":
            if not caddyfile_readable:
                add_error(
                    "Existing Caddyfile must be readable before it can be backed up and removed.",
                    "caddyfile_path",
                )
            elif not caddyfile_writable:
                add_error(
                    "Existing Caddyfile must be writable so CaddyBuddy can prevent duplicate active configuration.",
                    "caddyfile_path",
                )
            else:
                add_error(caddyfile_error, "caddyfile_path")
        elif state.mode == "missing":
            warnings.append(caddyfile_error)
        else:
            add_error(caddyfile_error, "caddyfile_path")

    if state.mode in _default_config_modes() and not default_config_exists:
        add_error("Default config /opt/caddybuddy/Caddyfile does not exist.", "caddyfile_path")
    elif state.mode in _default_config_modes():
        try:
            await asyncio.to_thread(_read_default_config_sync)
        except ValueError as exc:
            add_error(str(exc), "caddyfile_path")

    state.status = "failed" if errors else "in_progress"
    state.admin_api_url = normalized_admin_url
    state.acme_email = normalized_email
    state.caddyfile_path = normalized_caddyfile_path
    state.caddy_version = version
    state.backup_path = None
    state.last_preflight_at = _now_iso()
    # Drives wizard step routing: only a clean preflight advances to step 3 (review/execute).
    # A failed preflight keeps the user on step 2 to fix the offending fields. This flag is
    # intentionally not cleared by an execution failure, so retries stay on step 3.
    state.preflight_passed = not errors
    state.error_message = "; ".join(errors) if errors else None
    state.preflight_errors = errors
    state.preflight_warnings = warnings
    state.field_errors = field_errors
    state.field_check_statuses = {
        "admin_api_url": "failed" if "admin_api_url" in field_errors else "passed",
        "acme_email": "failed" if "acme_email" in field_errors else "passed",
        "caddyfile_path": "failed" if "caddyfile_path" in field_errors else "passed",
    }
    state.field_check_values = {
        "admin_api_url": normalized_admin_url,
        "acme_email": normalized_email,
        "caddyfile_path": normalized_caddyfile_path,
    }
    state.admin_api_reachable = reachable
    state.admin_config_readable = config_readable
    state.caddyfile_readable = caddyfile_readable
    state.caddyfile_writable = caddyfile_writable
    state.default_config_exists = default_config_exists
    state.api_only_takeover = docker_api_only

    # Offer the step-2 "Enable Admin API" assist only when an unreachable Admin API is the *sole*
    # blocker, the Caddyfile can be safely rewritten, and a restart-capable supervisor exists. This
    # flag is UI-only; enable_admin_api_and_reprobe recomputes every condition before acting.
    admin_api_only_blocker = bool(errors) and set(field_errors) <= {"admin_api_url"}
    replaceable = False
    if admin_api_only_blocker and not reachable and admin_url_valid and caddyfile_writable:
        replaceable, _replace_error = await asyncio.to_thread(
            _caddyfile_atomically_replaceable_sync, normalized_caddyfile_path
        )
    state.admin_api_assist_available = (
        not reachable
        and admin_url_valid
        and admin_api_only_blocker
        and state.mode in _ADMIN_API_ASSIST_MODES
        and caddyfile_writable
        and replaceable
        and get_settings().caddy_control_mode != "disabled"
    )

    await save_onboarding_state(session, state)
    return state


class _AssistError(RuntimeError):
    """Internal: a post-modification failure during assisted Admin-API enablement.

    Carries a user-facing message and shares the single rollback path with unexpected errors.
    """


async def enable_admin_api_and_reprobe(session: AsyncSession) -> OnboardingWizardState:
    """Edit the Caddyfile to enable Caddy's Admin API, restart Caddy, and re-run preflight.

    Recomputes every safety condition from scratch — the persisted ``admin_api_assist_available``
    flag is never trusted here. Pre-side-effect problems raise ``ValueError`` (the caller does a DB
    rollback). Once the Caddyfile is modified, every failure restores the original bytes and
    restarts Caddy before marking onboarding failed.
    """
    state = await lock_onboarding_state(session)

    # --- Pre-side-effect validation (ValueError -> route DB rollback + danger flash) -------------
    if state.mode not in _ADMIN_API_ASSIST_MODES:
        raise ValueError("Enabling the Admin API is only available for host or existing-config onboarding.")
    if state.status == "completed":
        raise ValueError("Caddy onboarding is already completed.")
    if get_settings().caddy_control_mode == "disabled":
        raise ValueError("Caddy restart capability is not configured, so the Admin API cannot be enabled automatically.")
    try:
        supervisor = await get_caddy_supervisor(session)
    except ValueError as exc:
        raise ValueError(f"Caddy restart capability is misconfigured: {exc}") from exc
    if isinstance(supervisor, DisabledSupervisor):
        raise ValueError("Caddy restart capability is not configured, so the Admin API cannot be enabled automatically.")

    try:
        normalized_url = normalize_caddy_api_url(state.admin_api_url)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    parsed = urlsplit(normalized_url)
    if parsed.hostname is None:
        raise ValueError("Caddy Admin API URL must include a host.")
    if not _is_loopback_host(parsed.hostname):
        raise ValueError("Automatic Admin API enablement may only bind Caddy Admin API to localhost.")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    admin_endpoint = f"{host}:{parsed.port or 2019}"

    _readable, writable, caddyfile_error = await asyncio.to_thread(
        _inspect_caddyfile_path, state.caddyfile_path
    )
    if not writable:
        raise ValueError(caddyfile_error or "Caddyfile is not writable.")
    replaceable, replace_error = await asyncio.to_thread(
        _caddyfile_atomically_replaceable_sync, state.caddyfile_path
    )
    if not replaceable:
        raise ValueError(replace_error or "Caddyfile cannot be safely replaced.")

    # Shortcut: the API is already reachable (e.g. enabled out of band) -> no file change/restart.
    reachable, _version, _config_readable, _api_error = await _probe_admin_api(normalized_url)
    if reachable:
        return await run_onboarding_preflight(
            session,
            admin_api_url=state.admin_api_url,
            acme_email=state.acme_email,
            caddyfile_path=state.caddyfile_path,
        )

    # --- Side-effect block: everything below is rollback-aware -----------------------------------
    backup_path: str | None = None
    try:
        backup_path = await asyncio.to_thread(
            _enable_admin_in_caddyfile_sync, state.caddyfile_path, admin_endpoint
        )
        restart = await supervisor.restart()
        if not restart.success:
            raise _AssistError(restart.error or "Could not restart Caddy to enable the Admin API.")

        for _ in range(_ADMIN_API_ENABLE_POLL_ATTEMPTS):
            await asyncio.sleep(_ADMIN_API_ENABLE_POLL_SECONDS)
            reachable, _version, _config_readable, _api_error = await _probe_admin_api(normalized_url)
            if reachable:
                break
        else:
            raise _AssistError(
                "Enabled the admin directive and restarted Caddy, but the Admin API is still unreachable."
            )

        return await run_onboarding_preflight(
            session,
            admin_api_url=state.admin_api_url,
            acme_email=state.acme_email,
            caddyfile_path=state.caddyfile_path,
        )
    except Exception as exc:  # noqa: BLE001 - convert any post-modification failure into a rollback
        if not isinstance(exc, _AssistError):
            logger.exception("Unexpected failure while enabling the Caddy Admin API.")
        base_message = str(exc) if isinstance(exc, _AssistError) else "Failed to enable the Caddy Admin API."
        rollback_message = ""
        retained_backup_path: str | None = None
        if backup_path is not None:
            try:
                await asyncio.to_thread(_restore_caddyfile_sync, state.caddyfile_path, backup_path)
            except Exception:
                logger.exception("Failed to restore the Caddyfile after a failed Admin API enablement.")
                rollback_message = (
                    " The original Caddyfile could not be restored automatically; manual recovery may be required."
                )
                retained_backup_path = backup_path
            else:
                restart_after_rollback = await supervisor.restart()
                if not restart_after_rollback.success:
                    rollback_message = (
                        " The Caddyfile was restored but Caddy could not be restarted; manual recovery may be required."
                    )
                    retained_backup_path = backup_path

        failed_state = await _mark_onboarding_failed(
            session,
            state,
            f"{base_message}{rollback_message}",
            exclusive_manager_confirmed=False,
        )
        if retained_backup_path is not None:
            failed_state.backup_path = retained_backup_path
            await save_onboarding_state(session, failed_state)
        return failed_state


async def execute_onboarding(
    session: AsyncSession,
    *,
    exclusive_manager_confirmed: bool,
) -> OnboardingWizardState:
    state = await lock_onboarding_state(session)
    if not state.preflight_ok:
        raise ValueError("Run a successful preflight before executing onboarding.")
    if state.status == "completed":
        raise ValueError("Caddy onboarding is already completed.")
    if not exclusive_manager_confirmed:
        raise ValueError("Confirm that CaddyBuddy will be the exclusive Caddy configuration manager.")
    state = await run_onboarding_preflight(
        session,
        admin_api_url=state.admin_api_url,
        acme_email=state.acme_email,
        caddyfile_path=state.caddyfile_path,
    )
    if not state.preflight_ok:
        return state
    if state.mode == "missing":
        async with session.begin_nested():
            await set_caddy_api_url(session, state.admin_api_url)
            await set_caddyfile_path(session, state.caddyfile_path)
            if state.acme_email:
                await set_ssllabs_email(session, state.acme_email)
        state.status = "completed"
        state.completed_at = _now_iso()
        state.error_message = None
        state.preflight_errors = []
        state.field_errors = {}
        state.exclusive_manager_confirmed = True
        await save_onboarding_state(session, state)
        return state
    if _requires_writable_caddyfile(state.mode) and not state.caddyfile_writable:
        return await _mark_onboarding_failed(
            session,
            state,
            "Caddyfile cleanup is required before CaddyBuddy can become the exclusive manager.",
            exclusive_manager_confirmed=exclusive_manager_confirmed,
        )

    backup_path: str | None = None
    prepared_default_config = False

    # Prepare file system changes before the DB savepoint so each has its own rollback path.
    if state.mode in _default_config_modes():
        try:
            backup_path = await asyncio.to_thread(
                _prepare_default_config_sync,
                state.caddyfile_path,
                acme_email=state.acme_email,
                admin_api_url=state.admin_api_url,
            )
            prepared_default_config = True
            if backup_path is not None:
                state.backup_path = backup_path
                await save_onboarding_state(session, state)
                await session.flush()
        except (ValueError, OSError) as exc:
            logger.warning("Default config preparation failed: %s", exc)
            return await _mark_onboarding_failed(
                session,
                state,
                str(exc),
                exclusive_manager_confirmed=exclusive_manager_confirmed,
            )

    # Runtime settings + API call in a savepoint: rollback automatically reverts DB on failure.
    error_message = ""
    try:
        async with session.begin_nested():
            await set_caddy_api_url(session, state.admin_api_url)
            await set_caddyfile_path(session, state.caddyfile_path)
            if state.acme_email:
                await set_ssllabs_email(session, state.acme_email)
            result = await onboard_caddy(session)
            if not onboarding_succeeded(result.status):
                raise ValueError(result.error or "Caddy onboarding failed.")
    except ValueError as exc:
        error_message = str(exc)
    except (CaddyServiceError, OSError) as exc:
        logger.warning("Caddy onboarding execution failed: %s", exc)
        error_message = "Caddy onboarding failed."
    except Exception:
        logger.exception("Unexpected Caddy onboarding execution failure.")
        error_message = "Caddy onboarding failed."
    else:
        state.status = "completed"
        state.completed_at = _now_iso()
        state.error_message = None
        state.preflight_errors = []
        state.field_errors = {}
        state.exclusive_manager_confirmed = True
        await save_onboarding_state(session, state)
        return state

    if prepared_default_config:
        try:
            await asyncio.to_thread(_rollback_prepared_default_config_sync, state.caddyfile_path, backup_path)
        except (ValueError, OSError):
            logger.exception("Failed to restore Caddyfile after onboarding error.")
            error_message = f"{error_message} Original Caddyfile could not be restored automatically."

    return await _mark_onboarding_failed(
        session,
        state,
        error_message,
        exclusive_manager_confirmed=exclusive_manager_confirmed,
    )
