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


def test_runtime_application_payload_has_stable_readable_modes() -> None:
    """Host read/write bits must not make modules unreadable to UID 1000."""
    dockerfile = Path("docker/Dockerfile").read_text(encoding="utf-8")

    assert "find app -type d -exec chmod 0755 {} +" in dockerfile
    assert "find app -type f -exec chmod 0644 {} +" in dockerfile
    assert "chmod 0644 run.py pyproject.toml" in dockerfile


def test_runtime_image_ships_the_changelog_the_about_page_renders() -> None:
    """The About page reads CHANGELOG.md from base_dir, so the image must contain it."""
    dockerfile = Path("docker/Dockerfile").read_text(encoding="utf-8")
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "COPY --chmod=0644 CHANGELOG.md ./CHANGELOG.md" in dockerfile
    assert "CHANGELOG.md" not in [line.strip() for line in dockerignore], (
        ".dockerignore excludes CHANGELOG.md; the About page would show 'Changelog not found'"
    )


def test_runtime_venv_is_checked_in_the_builder_and_pip_is_stripped_from_both_interpreters() -> None:
    """pip must be gone from the final image entirely, not just the copied venv.

    pip vendors its own dependencies (e.g. setuptools, msgpack) under pip/_vendor
    with a CycloneDX manifest that vulnerability scanners read as an installed-
    package inventory, reporting the vendored versions as application
    dependencies. The runtime stage starts fresh from `base`, so its own
    /usr/local interpreter still carries pip even after the builder's /opt/venv
    copy has had it removed - both must be stripped.
    """
    dockerfile = Path("docker/Dockerfile").read_text(encoding="utf-8")

    runtime_stage = dockerfile.split("FROM base AS runtime", maxsplit=1)[1]

    assert "python -m pip check" in dockerfile
    assert dockerfile.index("python -m pip check") < dockerfile.index("FROM base AS runtime")
    assert "/opt/venv/bin/python -m pip uninstall -y --root-user-action=ignore pip" not in dockerfile, (
        "the builder venv's pip is removed via 'python -m pip uninstall' while /opt/venv is still on PATH"
    )
    assert dockerfile.count("pip uninstall -y --root-user-action=ignore pip") == 2, (
        "expected exactly one pip removal in the builder stage (for /opt/venv) and "
        "one in the runtime stage (for the base image's /usr/local interpreter)"
    )
    assert "/usr/local/bin/python3 -m pip uninstall -y --root-user-action=ignore pip" in runtime_stage


def test_runtime_healthcheck_requires_a_direct_success_response() -> None:
    dockerfile = Path("docker/Dockerfile").read_text(encoding="utf-8")

    assert "http.client as h" in dockerfile
    assert "h.HTTPConnection('127.0.0.1', int(port), timeout=3)" in dockerfile
    assert "response.status == 200" in dockerfile


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
