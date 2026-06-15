#!/usr/bin/env python3
#
# tests/test_dockerfile.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from pathlib import Path


def test_runtime_image_does_not_install_caddy_binary() -> None:
    dockerfile = Path("docker/Dockerfile").read_text(encoding="utf-8")

    forbidden = [
        "ARG CADDY_VERSION",
        "cloudsmith.io/public/caddy",
        "apt-get install --no-install-recommends -y caddy",
        '"caddy=${CADDY_VERSION}',
    ]

    for token in forbidden:
        assert token not in dockerfile, (
            f"Dockerfile contains forbidden Caddy installation token: {token!r}"
        )


def test_dockerfile_cleans_apt_lists_after_install() -> None:
    dockerfile = Path("docker/Dockerfile").read_text(encoding="utf-8")

    cleanup_token = "rm -rf /var/lib/apt/lists/*"
    assert cleanup_token in dockerfile, "Dockerfile should clean apt lists after installation"


def test_caddy_service_does_not_use_local_caddy_cli() -> None:
    source = Path("app/services/caddy.py").read_text(encoding="utf-8")

    forbidden = [
        'shutil.which("caddy")',
        "create_subprocess_exec",
        '["fmt"',
        '["adapt"',
    ]

    for token in forbidden:
        assert token not in source, (
            f"caddy.py uses forbidden local CLI token: {token!r}"
        )
