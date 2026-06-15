#!/usr/bin/env python3
#
# tests/test_supervisor.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import asyncio
import os
import stat
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pathlib import Path
from app.services.supervisor import (
    DisabledSupervisor,
    DockerSupervisor,
    ScriptSupervisor,
    SystemdSupervisor,
    _communicate_with_timeout,
    get_caddy_supervisor,
)

class MockProcess:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False
        self.wait_called = False

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        self.wait_called = True

@pytest.mark.anyio
async def test_disabled_supervisor():
    supervisor = DisabledSupervisor()
    res = await supervisor.restart()
    assert not res.success
    assert "Caddy restart capability is not configured" in res.error

    res = await supervisor.reload()
    assert not res.success
    assert "Caddy reload capability is not configured" in res.error

    res = await supervisor.status()
    assert res.success
    assert res.status == "disabled"

@pytest.mark.anyio
async def test_systemd_supervisor():
    supervisor = SystemdSupervisor("caddy.service", 5.0)
    
    # Test restart
    mock_process = MockProcess(returncode=0, stdout=b"restarted", stderr=b"")
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        res = await supervisor.restart()
        assert res.success
        assert res.output == "restarted"
        mock_exec.assert_called_with(
            "sudo", "-n", "/bin/systemctl", "restart", "caddy.service",
            stdout=-1, stderr=-1
        )

    # Test status running
    mock_process = MockProcess(returncode=0, stdout=b"active", stderr=b"")
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        res = await supervisor.status()
        assert res.success
        assert res.status == "running"

    # Test status stopped
    mock_process = MockProcess(returncode=0, stdout=b"inactive", stderr=b"")
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        res = await supervisor.status()
        assert res.success
        assert res.status == "stopped"

    # Test status stopped even if systemctl returns non-zero for inactive
    mock_process = MockProcess(returncode=3, stdout=b"inactive", stderr=b"")
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        res = await supervisor.status()
        assert res.success
        assert res.status == "stopped"

    mock_process = MockProcess(returncode=1, stdout=b"", stderr=b"sudo: a password is required")
    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        res = await supervisor.status()
        assert not res.success
        assert res.status == "unknown"
        assert res.error == "Failed to check Caddy service status."

@pytest.mark.anyio
async def test_docker_supervisor():
    supervisor = DockerSupervisor("caddy", 5.0, "/custom/Caddyfile")

    # Test restart
    mock_process = MockProcess(returncode=0, stdout=b"caddy", stderr=b"")
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        res = await supervisor.restart()
        assert res.success
        assert res.output == "caddy"
        mock_exec.assert_called_with(
            "docker", "restart", "caddy",
            stdout=-1, stderr=-1
        )

    # Test status
    mock_process = MockProcess(returncode=0, stdout=b"running", stderr=b"")
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        res = await supervisor.status()
        assert res.success
        assert res.status == "running"

    mock_process = MockProcess(returncode=0, stdout=b"reloaded", stderr=b"")
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        res = await supervisor.reload()
        assert res.success
        mock_exec.assert_called_with(
            "docker", "exec", "caddy", "caddy", "reload", "--config", "/custom/Caddyfile",
            stdout=-1, stderr=-1
        )


def test_docker_supervisor_rejects_relative_caddyfile_path():
    with pytest.raises(ValueError, match="absolute Caddyfile path"):
        DockerSupervisor("caddy", 5.0, "relative/Caddyfile")

@pytest.mark.anyio
async def test_script_supervisor_validation(tmp_path):
    from types import SimpleNamespace
    original_stat = Path.stat
    def mocked_stat(self, *args, **kwargs):
        res = original_stat(self, *args, **kwargs)
        mode = res.st_mode if self.name == "writable.sh" else (res.st_mode & ~0o022)
        return SimpleNamespace(st_uid=0, st_mode=mode)

    with (
        patch("app.services.supervisor._ALLOWED_SCRIPT_ROOTS", (tmp_path.resolve(),)),
        patch.object(Path, "stat", mocked_stat),
    ):
        # Non-absolute path
        with pytest.raises(ValueError, match="must be an absolute path"):
            ScriptSupervisor("relative/path/to/script.sh", 5.0)

        # Non-existent path
        non_existent = tmp_path / "does_not_exist.sh"
        with pytest.raises(ValueError, match="does not exist"):
            ScriptSupervisor(str(non_existent), 5.0)

        # Not a file
        directory_path = tmp_path / "subdir"
        directory_path.mkdir()
        with pytest.raises(ValueError, match="is not a file"):
            ScriptSupervisor(str(directory_path), 5.0)

        # Group/Other writable
        writable_file = tmp_path / "writable.sh"
        writable_file.write_text("#!/bin/sh\necho ok")
        os.chmod(writable_file, stat.S_IRWXU | stat.S_IWGRP)  # Writable by group
        with pytest.raises(ValueError, match="writable by group or others"):
            ScriptSupervisor(str(writable_file), 5.0)

        # Correct file permissions
        secure_file = tmp_path / "secure.sh"
        secure_file.write_text("#!/bin/sh\necho ok")
        os.chmod(secure_file, stat.S_IRWXU)  # 0o700, owner only
        supervisor = ScriptSupervisor(str(secure_file), 5.0)
        assert supervisor.script_path == str(secure_file.resolve())

@pytest.mark.anyio
async def test_script_supervisor_rejects_writable_parent_directory(tmp_path):
    secure_file = tmp_path / "secure.sh"
    secure_file.write_text("#!/bin/sh\necho ok")
    os.chmod(secure_file, stat.S_IRWXU)

    original_stat = Path.stat

    def mocked_stat(self, *args, **kwargs):
        result = original_stat(self, *args, **kwargs)
        if self == tmp_path:
            return SimpleNamespace(st_uid=0, st_mode=result.st_mode | stat.S_IWGRP)
        return SimpleNamespace(st_uid=0, st_mode=result.st_mode)

    with (
        patch("app.services.supervisor._ALLOWED_SCRIPT_ROOTS", (tmp_path.resolve(),)),
        patch.object(Path, "stat", mocked_stat),
    ):
        with pytest.raises(ValueError, match="Script parent directory is writable by group or others"):
            ScriptSupervisor(str(secure_file), 5.0)

@pytest.mark.anyio
async def test_script_supervisor_rejects_non_root_owner(tmp_path):
    secure_file = tmp_path / "secure.sh"
    secure_file.write_text("#!/bin/sh\necho ok")
    os.chmod(secure_file, stat.S_IRWXU)
    
    from types import SimpleNamespace
    with (
        patch("app.services.supervisor._ALLOWED_SCRIPT_ROOTS", (tmp_path.resolve(),)),
        patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=1000, st_mode=0o100700)),
    ):
        with pytest.raises(ValueError, match="must be owned by root"):
            ScriptSupervisor(str(secure_file), 5.0)

@pytest.mark.anyio
async def test_script_supervisor_execution(tmp_path):
    secure_file = tmp_path / "secure.sh"
    secure_file.write_text("#!/bin/sh\necho ok")
    os.chmod(secure_file, stat.S_IRWXU)
    
    from types import SimpleNamespace
    with (
        patch("app.services.supervisor._ALLOWED_SCRIPT_ROOTS", (tmp_path.resolve(),)),
        patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=0, st_mode=0o100700)),
    ):
        supervisor = ScriptSupervisor(str(secure_file), 5.0)

        # Test restart
        mock_process = MockProcess(returncode=0, stdout=b"restarted via script", stderr=b"")
        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            res = await supervisor.restart()
            assert res.success
            assert res.output == "restarted via script"
            mock_exec.assert_called_with(
                str(secure_file.resolve()), "restart",
                stdout=-1, stderr=-1
            )

        # Test status
        mock_process = MockProcess(returncode=0, stdout=b"running", stderr=b"")
        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            res = await supervisor.status()
            assert res.success
            assert res.status == "running"

        mock_process = MockProcess(returncode=0, stdout=b"ok", stderr=b"")
        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            res = await supervisor.status()
            assert res.success
            assert res.status == "running"

        mock_process = MockProcess(returncode=0, stdout=b"not running", stderr=b"")
        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            res = await supervisor.status()
            assert not res.success
            assert res.status == "unknown"
            assert res.error == "Control script status returned unexpected output."


@pytest.mark.anyio
async def test_systemd_supervisor_returns_generic_message_on_restart_exception():
    supervisor = SystemdSupervisor("caddy.service", 5.0)

    with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("boom")):
        res = await supervisor.restart()

    assert not res.success
    assert res.error == "Failed to execute systemctl restart."

@pytest.mark.anyio
async def test_script_supervisor_rejects_paths_outside_allowed_roots(tmp_path):
    secure_file = tmp_path / "secure.sh"
    secure_file.write_text("#!/bin/sh\necho ok")
    os.chmod(secure_file, stat.S_IRWXU)

    with patch("app.services.supervisor._ALLOWED_SCRIPT_ROOTS", (Path("/etc/caddybuddy"),)):
        with pytest.raises(ValueError, match="outside allowed control directories"):
            ScriptSupervisor(str(secure_file), 5.0)

@pytest.mark.anyio
async def test_script_supervisor_stops_parent_validation_at_allowed_root(tmp_path):
    secure_file = tmp_path / "secure.sh"
    secure_file.write_text("#!/bin/sh\necho ok")
    os.chmod(secure_file, stat.S_IRWXU)

    original_stat = Path.stat

    def mocked_stat(self, *args, **kwargs):
        result = original_stat(self, *args, **kwargs)
        if self == tmp_path.parent:
            return SimpleNamespace(st_uid=1000, st_mode=result.st_mode)
        return SimpleNamespace(st_uid=0, st_mode=result.st_mode)

    with (
        patch("app.services.supervisor._ALLOWED_SCRIPT_ROOTS", (tmp_path.resolve(),)),
        patch.object(Path, "stat", mocked_stat),
    ):
        supervisor = ScriptSupervisor(str(secure_file), 5.0)

    assert supervisor.script_path == str(secure_file.resolve())

@pytest.mark.anyio
async def test_communicate_with_timeout_kills_and_reaps_process_on_timeout():
    class HangingProcess(MockProcess):
        async def communicate(self):
            await asyncio.sleep(1)
            return b"", b""

    process = HangingProcess()

    with pytest.raises(asyncio.TimeoutError):
        await _communicate_with_timeout(process, 0.01)

    assert process.killed is True
    assert process.wait_called is True

@pytest.mark.anyio
async def test_get_caddy_supervisor_uses_runtime_caddyfile_for_docker():
    settings = SimpleNamespace(
        caddy_control_mode="docker",
        caddy_control_timeout_seconds=5.0,
        caddy_docker_container="caddy",
        caddyfile_path=Path("/default/Caddyfile"),
    )
    session = AsyncMock()

    with (
        patch("app.services.supervisor.get_settings", return_value=settings),
        patch("app.services.supervisor.get_caddy_config", new=AsyncMock(return_value=SimpleNamespace(caddyfile_path=Path("/runtime/Caddyfile")))),
    ):
        supervisor = await get_caddy_supervisor(session)

    assert isinstance(supervisor, DockerSupervisor)
    assert supervisor.caddyfile_path == "/runtime/Caddyfile"
