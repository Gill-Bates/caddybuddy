#!/usr/bin/env python3
#
# tests/test_docker_workflow.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from pathlib import Path


def test_smoke_tests_explicitly_reject_caddy_binary_in_runtime_image() -> None:
    workflow = Path(".github/workflows/docker-build.yml").read_text(encoding="utf-8")

    for token in (
        "command -v caddy",
        "caddy binary must not be installed in the CaddyBuddy image",
        "caddy binary is not installed",
    ):
        assert token in workflow, f"Workflow is missing required smoke-test guard: {token!r}"
