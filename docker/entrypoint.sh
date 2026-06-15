#!/usr/bin/env bash
#
# docker/entrypoint.sh
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

# CaddyBuddy Container Entrypoint
# Runs as root to bootstrap directories/permissions, then drops to app user.
#

set -eu

APP_UID=1000
APP_GID=1000
DATA_PATH="/app/data"
CADDYFILE_PATH="/app/Caddyfile"
DEFAULT_CERT_PATH="/var/lib/caddy/.local/share/caddy/certificates"

# -----------------------------------------------------------------------------
# Bootstrap: Create required directories and fix ownership (runs as root)
# -----------------------------------------------------------------------------
require_tools() {
    if ! command -v gosu >/dev/null 2>&1; then
        echo "ERROR: gosu is required but not installed." >&2
        exit 1
    fi
}

has_setfacl() {
    command -v setfacl >/dev/null 2>&1
}

bootstrap_data_directory() {
    local current_owner

    mkdir -p "$DATA_PATH"

    current_owner="$(stat -c '%u:%g' "$DATA_PATH" 2>/dev/null || echo '')"
    if [ "$current_owner" != "${APP_UID}:${APP_GID}" ]; then
        chown -R "${APP_UID}:${APP_GID}" "$DATA_PATH"
    fi
}

# -----------------------------------------------------------------------------
# Verify mounted Caddyfile and writable runtime directories
# -----------------------------------------------------------------------------
verify_caddyfile_mount() {
    if [ ! -e "$CADDYFILE_PATH" ]; then
        echo "WARNING: Caddyfile is missing at $CADDYFILE_PATH." >&2
        echo "CaddyBuddy will start; the onboarding wizard can initialize or import the managed configuration later." >&2
        return 0
    fi

    if [ ! -f "$CADDYFILE_PATH" ]; then
        echo "WARNING: Caddyfile path is not a regular file: $CADDYFILE_PATH" >&2
        echo "CaddyBuddy will start; fix the mount if the managed Caddyfile is needed." >&2
        return 0
    fi

    if ! gosu "${APP_UID}:${APP_GID}" test -r "$CADDYFILE_PATH" 2>/dev/null; then
        echo "WARNING: Caddyfile is not readable by uid=${APP_UID}: $CADDYFILE_PATH" >&2
        echo "CaddyBuddy will start; update the host mount permissions if Caddyfile editing is required." >&2
    fi

    if ! gosu "${APP_UID}:${APP_GID}" test -w "$CADDYFILE_PATH" 2>/dev/null; then
        echo "WARNING: Caddyfile is not writable by uid=${APP_UID}: $CADDYFILE_PATH" >&2
        echo "CaddyBuddy will start; onboarding may still proceed in API-only mode." >&2
    fi
}


ensure_caddy_cert_permissions() {
    local cert_dir="${CB_CADDY_CERTIFICATES_PATH:-$DEFAULT_CERT_PATH}"
    local current

    case "$cert_dir" in
        /*) ;;
        *)
            echo "ERROR: CB_CADDY_CERTIFICATES_PATH must be an absolute path: $cert_dir" >&2
            exit 1
            ;;
    esac

    if [ "$cert_dir" = "/" ]; then
        echo "ERROR: refusing unsafe certificate storage path: $cert_dir" >&2
        exit 1
    fi

    if [ ! -e "$cert_dir" ]; then
        return 0
    fi

    if ! has_setfacl; then
        echo "WARNING: setfacl is not installed; certificate storage ACLs will not be modified." >&2
        echo "CaddyBuddy will start; certificate filesystem inspection may be unavailable." >&2
        return 0
    fi

    # Parent directories only need traversal, not listing.
    current="$cert_dir"
    while [ "$current" != "/" ]; do
        if [ -d "$current" ] && ! gosu "${APP_UID}:${APP_GID}" test -x "$current" 2>/dev/null; then
            setfacl -m "u:${APP_UID}:x" "$current" 2>/dev/null || true
        fi
        current="$(dirname "$current")"
    done

    # Existing certificate tree: allow directory traversal/listing and file reads.
    find "$cert_dir" -type d -exec setfacl -m "u:${APP_UID}:rx" '{}' + 2>/dev/null || true
    find "$cert_dir" -type f -exec setfacl -m "u:${APP_UID}:r" '{}' + 2>/dev/null || true

    # Future certificates: new issuer/domain folders and files inherit access.
    find "$cert_dir" -type d -exec setfacl -d -m "u:${APP_UID}:rx" '{}' + 2>/dev/null || true

    if ! gosu "${APP_UID}:${APP_GID}" test -x "$cert_dir" 2>/dev/null; then
        echo "ERROR: Caddy certificate storage is not traversable by uid=${APP_UID}: $cert_dir" >&2
        echo "Automatic repair of the mounted Caddy storage permissions did not succeed." >&2
        exit 1
    fi
}

verify_data_permissions() {
    local test_file="${DATA_PATH}/.write_test_$$"

    if ! gosu "${APP_UID}:${APP_GID}" touch "$test_file" 2>/dev/null; then
        echo "ERROR: $DATA_PATH is not writable by uid=${APP_UID}." >&2
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
python -c "from app.utils.banner import print_banner_once; print_banner_once()" || true
export CADDYBUDDY_BANNER_PRINTED=1
exec "$@"
' bash "$@"
}

if [ "$#" -eq 0 ]; then
    echo "ERROR: no command provided to entrypoint." >&2
    exit 1
fi

# Bootstrap as root
require_tools
bootstrap_data_directory
verify_caddyfile_mount
verify_data_permissions
ensure_caddy_cert_permissions

# Drop privileges and exec the main command as app user
exec_as_app "$@"
