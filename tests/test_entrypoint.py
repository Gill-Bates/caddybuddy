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
    assert 'find "$cert_dir" -type f -exec setfacl -m "u:${APP_UID}:r"' in source
    assert 'find "$cert_dir" -type d -exec setfacl -m "u:${APP_UID}:rwx"' not in source
    assert 'test -w "$cert_dir"' not in source
    assert 'sudo chown ${APP_UID}:${APP_GID} /path/to/Caddyfile' not in source


def test_entrypoint_skips_certificate_acl_repair_when_paths_are_missing() -> None:
    source = Path("docker/entrypoint.sh").read_text(encoding="utf-8")

    assert 'if [ ! -e "$cert_dir" ]; then' in source
    assert 'CaddyBuddy will start; the onboarding wizard can initialize or import the managed configuration later.' in source
    assert 'WARNING: Caddyfile is missing at $CADDYFILE_PATH.' in source
