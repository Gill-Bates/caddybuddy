#!/usr/bin/env python3
#
# app/services/supervisor.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import asyncio
import logging
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config.settings import get_settings
from app.services.runtime_settings import get_caddy_config


logger = logging.getLogger(__name__)
_ALLOWED_SCRIPT_ROOTS = (
    Path("/app").resolve(strict=False),
    Path("/opt/caddybuddy").resolve(strict=False),
    Path("/usr/local/lib/caddybuddy").resolve(strict=False),
    Path("/etc/caddybuddy").resolve(strict=False),
)
_SCRIPT_RUNNING_OUTPUTS = {"running", "active", "ok"}
_SCRIPT_STOPPED_OUTPUTS = {"stopped", "inactive", "failed"}
_SYSTEMD_STOPPED_OUTPUTS = {"inactive", "failed", "unknown", "deactivating", "activating"}


async def _communicate_with_timeout(process: asyncio.subprocess.Process, timeout: float) -> tuple[bytes, bytes]:
    try:
        return await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        with suppress(Exception):
            await process.wait()
        raise


@dataclass(slots=True, frozen=True)
class RestartResult:
    success: bool
    output: str
    error: str | None = None


@dataclass(slots=True, frozen=True)
class StatusResult:
    success: bool
    status: str  # "running" | "stopped" | "disabled" | "unknown"
    error: str | None = None


class CaddySupervisor(Protocol):
    async def restart(self) -> RestartResult:
        ...

    async def reload(self) -> RestartResult:
        ...

    async def status(self) -> StatusResult:
        ...


class DisabledSupervisor:
    async def restart(self) -> RestartResult:
        return RestartResult(success=False, output="", error="Caddy restart capability is not configured. Forced renewal for this state is unavailable from CaddyBuddy.")

    async def reload(self) -> RestartResult:
        return RestartResult(success=False, output="", error="Caddy reload capability is not configured.")

    async def status(self) -> StatusResult:
        return StatusResult(success=True, status="disabled")


class SystemdSupervisor:
    def __init__(self, unit: str, timeout: float):
        if not re.match(r"^[a-zA-Z0-9_.@-]+\.service$|^[a-zA-Z0-9_.@-]+$", unit):
            raise ValueError(f"Invalid systemd unit name: {unit}")
        if not unit.endswith(".service"):
            unit = f"{unit}.service"
        self.unit = unit
        self.timeout = timeout

    async def _run_systemctl(self, action: str) -> RestartResult:
        try:
            process = await asyncio.create_subprocess_exec(
                "sudo", "-n", "/bin/systemctl", action, self.unit,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await _communicate_with_timeout(process, self.timeout)
            except asyncio.TimeoutError:
                return RestartResult(success=False, output="", error=f"Timeout executing systemctl {action}")

            success = process.returncode == 0
            output = stdout.decode().strip()
            error = None
            if not success:
                logger.warning(
                    "systemctl %s failed for %s: %s",
                    action,
                    self.unit,
                    stderr.decode().strip() or output,
                )
                error = f"Failed to execute systemctl {action}."
            return RestartResult(success=success, output=output, error=error)
        except Exception:
            logger.exception("Failed to run systemctl %s", action)
            return RestartResult(success=False, output="", error=f"Failed to execute systemctl {action}.")

    async def restart(self) -> RestartResult:
        return await self._run_systemctl("restart")

    async def reload(self) -> RestartResult:
        return await self._run_systemctl("reload")

    async def status(self) -> StatusResult:
        try:
            process = await asyncio.create_subprocess_exec(
                "sudo", "-n", "/bin/systemctl", "is-active", self.unit,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await _communicate_with_timeout(process, self.timeout)
            except asyncio.TimeoutError:
                return StatusResult(success=False, status="unknown", error="Timeout checking systemctl is-active")

            output = stdout.decode().strip().lower()
            if output == "active":
                return StatusResult(success=True, status="running")
            if output in _SYSTEMD_STOPPED_OUTPUTS:
                return StatusResult(success=True, status="stopped")
            if process.returncode != 0:
                stderr_text = stderr.decode().strip()
                logger.warning(
                    "systemctl is-active failed for %s: %s",
                    self.unit,
                    stderr_text or output,
                )
                return StatusResult(success=False, status="unknown", error="Failed to check Caddy service status.")
            return StatusResult(success=False, status="unknown", error=f"Unexpected status output: {output}")
        except Exception:
            logger.exception("Failed to check systemctl status")
            return StatusResult(success=False, status="unknown", error="Failed to check Caddy service status.")


class DockerSupervisor:
    def __init__(self, container: str, timeout: float, caddyfile_path: str):
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]+$", container):
            raise ValueError(f"Invalid docker container name: {container}")
        if not caddyfile_path:
            raise ValueError("Docker supervisor requires a Caddyfile path.")
        if not Path(caddyfile_path).is_absolute():
            raise ValueError("Docker supervisor requires an absolute Caddyfile path.")
        self.container = container
        self.timeout = timeout
        self.caddyfile_path = caddyfile_path

    async def _run_docker(self, action: str) -> RestartResult:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", action, self.container,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await _communicate_with_timeout(process, self.timeout)
            except asyncio.TimeoutError:
                return RestartResult(success=False, output="", error=f"Timeout executing docker {action}")

            success = process.returncode == 0
            output = stdout.decode().strip()
            error = None
            if not success:
                logger.warning(
                    "docker %s failed for %s: %s",
                    action,
                    self.container,
                    stderr.decode().strip() or output,
                )
                error = f"Failed to execute docker {action}."
            return RestartResult(success=success, output=output, error=error)
        except Exception:
            logger.exception("Failed to run docker %s", action)
            return RestartResult(success=False, output="", error=f"Failed to execute docker {action}.")

    async def restart(self) -> RestartResult:
        return await self._run_docker("restart")

    async def reload(self) -> RestartResult:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", "exec", self.container, "caddy", "reload", "--config", self.caddyfile_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await _communicate_with_timeout(process, self.timeout)
            except asyncio.TimeoutError:
                return RestartResult(success=False, output="", error="Timeout executing caddy reload inside docker")

            success = process.returncode == 0
            output = stdout.decode().strip()
            error = None
            if not success:
                logger.warning(
                    "caddy reload inside docker failed for %s: %s",
                    self.container,
                    stderr.decode().strip() or output,
                )
                error = "Failed to reload Caddy inside docker."
            return RestartResult(success=success, output=output, error=error)
        except Exception:
            logger.exception("Failed to exec caddy reload inside docker")
            return RestartResult(success=False, output="", error="Failed to reload Caddy inside docker.")

    async def status(self) -> StatusResult:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", "inspect", "-f", "{{.State.Status}}", self.container,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await _communicate_with_timeout(process, self.timeout)
            except asyncio.TimeoutError:
                return StatusResult(success=False, status="unknown", error="Timeout executing docker inspect")

            success = process.returncode == 0
            if not success:
                logger.warning(
                    "docker inspect failed for %s: %s",
                    self.container,
                    stderr.decode().strip(),
                )
                return StatusResult(
                    success=False,
                    status="unknown",
                    error="Failed to check Docker container status.",
                )

            output = stdout.decode().strip().lower()
            if output == "running":
                return StatusResult(success=True, status="running")
            elif output in ("exited", "created", "paused", "dead"):
                return StatusResult(success=True, status="stopped")
            else:
                return StatusResult(success=True, status="unknown", error=f"Container status is '{output}'")
        except Exception:
            logger.exception("Failed to check docker container status")
            return StatusResult(success=False, status="unknown", error="Failed to check Docker container status.")


class ScriptSupervisor:
    def __init__(self, script_path: str, timeout: float):
        resolved_path = _validate_script_path(script_path)
        self.script_path = str(resolved_path)
        self.timeout = timeout

    async def _run_script(self, action: str) -> RestartResult:
        try:
            process = await asyncio.create_subprocess_exec(
                self.script_path, action,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await _communicate_with_timeout(process, self.timeout)
            except asyncio.TimeoutError:
                return RestartResult(success=False, output="", error=f"Timeout executing script {action}")

            success = process.returncode == 0
            output = stdout.decode().strip()
            error = None
            if not success:
                logger.warning(
                    "Control script %s failed: %s",
                    action,
                    stderr.decode().strip() or output,
                )
                error = f"Failed to execute control script {action}."
            return RestartResult(success=success, output=output, error=error)
        except Exception:
            logger.exception("Failed to run control script for %s", action)
            return RestartResult(success=False, output="", error=f"Failed to execute control script {action}.")

    async def restart(self) -> RestartResult:
        return await self._run_script("restart")

    async def reload(self) -> RestartResult:
        return await self._run_script("reload")

    async def status(self) -> StatusResult:
        try:
            process = await asyncio.create_subprocess_exec(
                self.script_path, "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await _communicate_with_timeout(process, self.timeout)
            except asyncio.TimeoutError:
                return StatusResult(success=False, status="unknown", error="Timeout executing script status")

            success = process.returncode == 0
            output = stdout.decode().strip().lower()
            if success and output in _SCRIPT_RUNNING_OUTPUTS:
                return StatusResult(success=True, status="running")
            if success and output in _SCRIPT_STOPPED_OUTPUTS:
                return StatusResult(success=True, status="stopped")
            if success:
                logger.warning("Control script status returned unexpected output for %s: %s", self.script_path, output)
                return StatusResult(
                    success=False,
                    status="unknown",
                    error="Control script status returned unexpected output.",
                )
            logger.warning(
                "Control script status failed for %s: %s",
                self.script_path,
                stderr.decode().strip() or output,
            )
            return StatusResult(
                success=False,
                status="unknown",
                error="Control script status failed.",
            )
        except Exception:
            logger.exception("Failed to run control script for status")
            return StatusResult(success=False, status="unknown", error="Failed to check control script status.")


def _validate_secure_path_owner_and_mode(path: Path, label: str) -> None:
    stat_info = path.stat()
    if stat_info.st_uid != 0:
        raise ValueError(f"{label} must be owned by root: {path}")
    if stat_info.st_mode & 0o022:
        raise ValueError(f"{label} is writable by group or others: {path}")


def _allowed_script_root_for(resolved_path: Path) -> Path:
    for root in _ALLOWED_SCRIPT_ROOTS:
        if resolved_path.is_relative_to(root):
            return root
    allowed_roots = ", ".join(str(root) for root in _ALLOWED_SCRIPT_ROOTS)
    raise ValueError(
        f"Script path is outside allowed control directories: {resolved_path}. "
        f"Allowed roots: {allowed_roots}"
    )


def _validate_secure_parent_dirs(path: Path, *, stop_at: Path) -> None:
    for parent in (path, *path.parents):
        _validate_secure_path_owner_and_mode(parent, "Script parent directory")
        if parent == stop_at:
            break


def _validate_script_path(script_path: str) -> Path:
    path = Path(script_path)
    if not path.is_absolute():
        raise ValueError(f"Script path must be an absolute path: {script_path}")
    if not path.exists():
        raise ValueError(f"Script path does not exist: {script_path}")
    if not path.is_file():
        raise ValueError(f"Script path is not a file: {script_path}")

    resolved_path = path.resolve(strict=True)
    allowed_root = _allowed_script_root_for(resolved_path)

    try:
        _validate_secure_path_owner_and_mode(resolved_path, "Script file")
        _validate_secure_parent_dirs(resolved_path.parent, stop_at=allowed_root)
    except OSError as e:
        if isinstance(e, ValueError):
            raise
        raise ValueError(f"Failed to check script file permissions: {e}")

    return resolved_path


async def get_caddy_supervisor(session=None) -> CaddySupervisor:
    settings = get_settings()
    mode = settings.caddy_control_mode
    timeout = settings.caddy_control_timeout_seconds

    if mode == "systemd":
        return SystemdSupervisor(unit=settings.caddy_systemd_unit, timeout=timeout)
    elif mode == "docker":
        caddyfile_path = settings.caddyfile_path
        if session is not None:
            config = await get_caddy_config(session)
            caddyfile_path = config.caddyfile_path
        return DockerSupervisor(
            container=settings.caddy_docker_container,
            timeout=timeout,
            caddyfile_path=str(caddyfile_path or ""),
        )
    elif mode == "script":
        return ScriptSupervisor(script_path=settings.caddy_control_script, timeout=timeout)
    else:
        return DisabledSupervisor()
