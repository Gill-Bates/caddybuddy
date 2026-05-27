#!/usr/bin/env bash
#
# docker/entrypoint.sh
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

# CaddyBuddy Container Entrypoint
# Runs as root to bootstrap directories/permissions, then drops to app user.
#

set -eu

APP_USER="app"
APP_UID=1000
APP_GID=1000
CADDYFILE_PATH="/app/Caddyfile"

if [ "${CADDYBUDDY_MOUNTED_CADDYFILE_PATH:-$CADDYFILE_PATH}" != "$CADDYFILE_PATH" ]; then
    echo "ERROR: unsupported CADDYBUDDY_MOUNTED_CADDYFILE_PATH: ${CADDYBUDDY_MOUNTED_CADDYFILE_PATH}" >&2
    echo "Expected: $CADDYFILE_PATH" >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# Bootstrap: Create required directories and fix ownership (runs as root)
# -----------------------------------------------------------------------------
bootstrap_directories() {
    target="/app/data"

    if [ ! -d "$target" ]; then
        mkdir -p "$target"
    fi

    if [ "$(stat -c '%u:%g' "$target" 2>/dev/null)" != "${APP_UID}:${APP_GID}" ]; then
        chown -R "${APP_UID}:${APP_GID}" "$target"
    fi
}

# -----------------------------------------------------------------------------
# Verify mounted Caddyfile and writable runtime directories
# -----------------------------------------------------------------------------
verify_caddyfile_mount() {
    if [ ! -e "$CADDYFILE_PATH" ]; then
        echo "ERROR: required Caddyfile is missing: $CADDYFILE_PATH" >&2
        echo "Create and bind-mount the host Caddyfile before starting CaddyBuddy." >&2
        exit 1
    fi

    if [ ! -f "$CADDYFILE_PATH" ]; then
        echo "ERROR: Caddyfile path is not a regular file: $CADDYFILE_PATH" >&2
        exit 1
    fi

    # Fix ownership if running as root and file is not already owned by app user.
    # This handles the common case where the host file is owned by a different user.
    current_owner=$(stat -c '%u:%g' "$CADDYFILE_PATH" 2>/dev/null || echo "")
    if [ "$(id -u)" = "0" ] && [ -n "$current_owner" ] && [ "$current_owner" != "${APP_UID}:${APP_GID}" ]; then
        if chown "${APP_UID}:${APP_GID}" "$CADDYFILE_PATH" 2>/dev/null; then
            echo "INFO: Fixed Caddyfile ownership to ${APP_UID}:${APP_GID}"
        fi
    fi

    if ! gosu "${APP_UID}:${APP_GID}" test -r "$CADDYFILE_PATH" 2>/dev/null; then
        echo "ERROR: Caddyfile is not readable: $CADDYFILE_PATH" >&2
        exit 1
    fi

    if ! gosu "${APP_UID}:${APP_GID}" test -w "$CADDYFILE_PATH" 2>/dev/null; then
        echo "ERROR: Caddyfile is not writable: $CADDYFILE_PATH" >&2
        echo "Ensure the mounted Caddyfile is bind-mounted read-write and owned by UID ${APP_UID}." >&2
        echo "On the host, run: sudo chown ${APP_UID}:${APP_GID} /path/to/Caddyfile" >&2
        exit 1
    fi
}


verify_data_permissions() {
    target="/app/data"
    test_file="${target}/.write_test_$$"

    if ! gosu "${APP_UID}:${APP_GID}" touch "$test_file" 2>/dev/null; then
        echo "ERROR: $target is not writable by uid=${APP_UID}." >&2
        echo "Check volume ownership or mount options." >&2
        exit 1
    fi

    rm -f "$test_file"
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

exec_as_app() {
    exec gosu "${APP_UID}:${APP_GID}" /bin/bash -eu -c '
python -c "from app.utils.banner import print_banner_once; print_banner_once()"
export CADDYBUDDY_BANNER_PRINTED=1
exec "$@"
' bash "$@"
}

if [ "$#" -eq 0 ]; then
    echo "ERROR: no command provided to entrypoint." >&2
    exit 1
fi

# Bootstrap as root
bootstrap_directories
verify_caddyfile_mount
verify_data_permissions

# Drop privileges and exec the main command as app user
exec_as_app "$@"