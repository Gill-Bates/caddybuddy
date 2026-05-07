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

# -----------------------------------------------------------------------------
# Bootstrap: Create required directories and fix ownership (runs as root)
# -----------------------------------------------------------------------------
bootstrap_directories() {
    REQUIRED_DIRS="data"

    for dir in $REQUIRED_DIRS; do
        target="/app/${dir}"
        
        # Create directory if missing
        if [ ! -d "$target" ]; then
            mkdir -p "$target"
        fi
        
        # Fix ownership (idempotent - only changes if needed)
        if [ "$(stat -c '%u:%g' "$target" 2>/dev/null)" != "${APP_UID}:${APP_GID}" ]; then
            chown -R "${APP_UID}:${APP_GID}" "$target"
        fi
    done
}

# -----------------------------------------------------------------------------
# Verify write permissions (as app user)
# -----------------------------------------------------------------------------
verify_write_permissions() {
    WRITABLE_DIRS="data"

    for dir in $WRITABLE_DIRS; do
        target="/app/${dir}"
        test_file="${target}/.write_test_$$"
        
        if ! su -s /bin/sh "$APP_USER" -c "touch '$test_file'" 2>/dev/null; then
            echo "ERROR: $target is not writable by uid=${APP_UID}." >&2
            echo "This should not happen after chown. Check volume mount options." >&2
            exit 1
        fi
        rm -f "$test_file"
    done
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

# Print banner (as root, before dropping privileges)
python -c 'from app.utils.banner import print_banner_once; print_banner_once()'
export CADDYBUDDY_BANNER_PRINTED=1

# Bootstrap as root
bootstrap_directories
verify_write_permissions

# Drop privileges and exec the main command as app user
exec su -s /bin/sh "$APP_USER" -c "exec $*"