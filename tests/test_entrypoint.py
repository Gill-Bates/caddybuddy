#!/usr/bin/env python3
#
# tests/test_entrypoint.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from pathlib import Path


def test_entrypoint_grants_read_only_certificate_acls() -> None:
    source = Path("docker/entrypoint.sh").read_text(encoding="utf-8")

    assert "has_setfacl()" in source
    assert 'if ! has_setfacl; then' in source
    assert 'find "$cert_dir" -type d -exec setfacl -m "u:${APP_UID}:rx"' in source
    # Read access is granted only to *.crt files: private keys and ACME
    # account metadata (*.key, *.json) sit in the same tree and must stay
    # unreadable by the app user.
    assert (
        'find "$cert_dir" -type f -name \'*.crt\' -exec setfacl -m "u:${APP_UID}:r"'
        in source
    )
    assert 'find "$cert_dir" -type f -exec setfacl -m "u:${APP_UID}:r"' not in source
    assert 'find "$cert_dir" -type d -exec setfacl -m "u:${APP_UID}:rwx"' not in source
    assert 'test -w "$cert_dir"' not in source
    assert 'sudo chown ${APP_UID}:${APP_GID} /path/to/Caddyfile' not in source


def test_entrypoint_canonicalizes_cert_path_when_missing() -> None:
    """readlink -f prints nothing unless every parent component exists.

    Using it here aborted startup with "could not resolve" whenever no Caddy
    storage was mounted, because the default path's parents are absent. -m
    canonicalizes '..' and symlinks with no existence requirement, so the
    missing-path case reaches the "[ ! -e ]" no-op instead.
    """
    source = Path("docker/entrypoint.sh").read_text(encoding="utf-8")

    assert 'cert_dir="$(readlink -m -- "$cert_dir" 2>/dev/null || true)"' in source
    assert "readlink -f" not in source


def test_entrypoint_skips_certificate_acl_repair_when_paths_are_missing() -> None:
    source = Path("docker/entrypoint.sh").read_text(encoding="utf-8")

    assert 'if [ ! -e "$cert_dir" ]; then' in source
    assert 'CaddyBuddy will start; the onboarding wizard can initialize or import the managed configuration later.' in source
    assert 'WARNING: Caddyfile is missing at $CADDYFILE_PATH.' in source
