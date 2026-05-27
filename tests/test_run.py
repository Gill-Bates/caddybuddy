#!/usr/bin/env python3
#
# tests/test_run.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import run as run_module


class RunModuleTests(unittest.TestCase):
    def test_server_handle_exit_requests_event_bus_shutdown(self) -> None:
        server = object.__new__(run_module.CaddyBuddyServer)

        with (
            patch.object(run_module.event_bus, "request_shutdown") as request_shutdown,
            patch("uvicorn.Server.handle_exit") as super_handle_exit,
        ):
            run_module.CaddyBuddyServer.handle_exit(server, 2, None)

        request_shutdown.assert_called_once_with()
        super_handle_exit.assert_called_once_with(2, None)

    def test_main_uses_custom_server_class(self) -> None:
        settings = SimpleNamespace(
            host="127.0.0.1",
            port=8000,
            log_level="INFO",
            forwarded_allow_ips="*",
            reload=False,
        )

        fake_server = SimpleNamespace(run=lambda: None)

        with (
            patch.object(run_module, "get_settings", return_value=settings),
            patch.object(run_module, "print_banner_once"),
            patch.object(run_module.os.environ, "get", return_value="1"),
            patch.object(run_module, "CaddyBuddyServer", return_value=fake_server) as server_class,
            patch.object(run_module.uvicorn, "Config", return_value=SimpleNamespace()) as config_class,
            patch.object(run_module, "build_log_config", return_value={"version": 1}),
        ):
            run_module.main()

        config_class.assert_called_once()
        server_class.assert_called_once()

    def test_main_suppresses_keyboard_interrupt_from_server_run(self) -> None:
        settings = SimpleNamespace(
            host="127.0.0.1",
            port=8000,
            log_level="INFO",
            forwarded_allow_ips="*",
            reload=False,
        )

        fake_server = SimpleNamespace(run=lambda: (_ for _ in ()).throw(KeyboardInterrupt()))

        with (
            patch.object(run_module, "get_settings", return_value=settings),
            patch.object(run_module, "print_banner_once"),
            patch.object(run_module.os.environ, "get", return_value="1"),
            patch.object(run_module, "CaddyBuddyServer", return_value=fake_server) as server_class,
            patch.object(run_module.uvicorn, "Config", return_value=SimpleNamespace()) as config_class,
            patch.object(run_module, "build_log_config", return_value={"version": 1}),
        ):
            run_module.main()

        config_class.assert_called_once()
        server_class.assert_called_once()


if __name__ == "__main__":
    unittest.main()