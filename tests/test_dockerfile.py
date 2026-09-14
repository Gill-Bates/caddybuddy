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


def test_dockerfile_does_not_bake_apt_lists_into_layers() -> None:
    """Every apt-get invocation must keep /var/lib/apt on a cache mount.

    Package lists then live in the BuildKit cache rather than an image layer,
    which is why no `rm -rf /var/lib/apt/lists/*` cleanup appears (or would
    help: deleting inside a cache mount leaves the layer size unchanged).
    """
    dockerfile = Path("docker/Dockerfile").read_text(encoding="utf-8")

    apt_invocations = dockerfile.count("apt-get update")
    cache_mounts = dockerfile.count("--mount=type=cache,target=/var/lib/apt")

    assert apt_invocations > 0, "Dockerfile no longer runs apt-get at all"
    assert cache_mounts == apt_invocations, (
        f"{apt_invocations} 'apt-get update' invocation(s) but {cache_mounts} "
        "/var/lib/apt cache mount(s); package lists would land in an image layer"
    )


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
