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

    if ! command -v setfacl >/dev/null 2>&1; then
        echo "ERROR: setfacl is required to grant CaddyBuddy access without world-writable permissions." >&2
        echo "Install ACL support or set ownership/group permissions on the host." >&2
        exit 1
    fi
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
        echo "ERROR: required Caddyfile is missing: $CADDYFILE_PATH" >&2
        echo "Create and bind-mount the host Caddyfile before starting CaddyBuddy." >&2
        exit 1
    fi

    if [ ! -f "$CADDYFILE_PATH" ]; then
        echo "ERROR: Caddyfile path is not a regular file: $CADDYFILE_PATH" >&2
        exit 1
    fi

    if ! gosu "${APP_UID}:${APP_GID}" test -r "$CADDYFILE_PATH" 2>/dev/null; then
        echo "ERROR: Caddyfile is not readable by uid=${APP_UID}: $CADDYFILE_PATH" >&2
        echo "On the host, run for example:" >&2
        echo "  sudo chown ${APP_UID}:${APP_GID} /path/to/Caddyfile" >&2
        exit 1
    fi

    if ! gosu "${APP_UID}:${APP_GID}" test -w "$CADDYFILE_PATH" 2>/dev/null; then
        echo "ERROR: Caddyfile is not writable by uid=${APP_UID}: $CADDYFILE_PATH" >&2
        echo "Ensure the mounted Caddyfile is bind-mounted read-write." >&2
        echo "On the host, run for example:" >&2
        echo "  sudo chown ${APP_UID}:${APP_GID} /path/to/Caddyfile" >&2
        exit 1
    fi
}


ensure_caddy_cert_permissions() {
    local cert_path="${CB_CADDY_CERTIFICATES_PATH:-$DEFAULT_CERT_PATH}"
    local caddy_share
    local cert_root
    local current

    case "$cert_path" in
        /*) ;;
        *)
            echo "ERROR: CB_CADDY_CERTIFICATES_PATH must be an absolute path: $cert_path" >&2
            exit 1
            ;;
    esac

    cert_root="$(dirname "$cert_path")"
    caddy_share="$(dirname "$cert_root")"

    if [ "$cert_root" = "/" ] || [ "$caddy_share" = "/" ]; then
        echo "ERROR: refusing unsafe certificate storage path: $cert_path" >&2
        echo "Set CB_CADDY_CERTIFICATES_PATH to a specific directory under Caddy storage." >&2
        exit 1
    fi

    if [ ! -e "$caddy_share" ]; then
        return 0
    fi

    current="$caddy_share"
    while [ "$current" != "/" ]; do
        if [ -d "$current" ] && ! gosu "${APP_UID}:${APP_GID}" test -x "$current" 2>/dev/null; then
            setfacl -m "u:${APP_UID}:rx" "$current" 2>/dev/null || true
        fi
        current="$(dirname "$current")"
    done

    # Grant app write access only inside Caddy's storage root, not the parent share tree.
    find "$cert_root" -type d -exec setfacl -m "u:${APP_UID}:rwx" '{}' + 2>/dev/null || true

    if ! gosu "${APP_UID}:${APP_GID}" test -x "$cert_root" 2>/dev/null; then
        echo "ERROR: Caddy storage is not traversable by uid=${APP_UID}: $cert_root" >&2
        echo "Automatic repair of the mounted Caddy storage permissions did not succeed." >&2
        exit 1
    fi

    if ! gosu "${APP_UID}:${APP_GID}" test -w "$cert_root" 2>/dev/null; then
        echo "ERROR: Caddy storage is not writable by uid=${APP_UID}: $cert_root" >&2
        echo "Automatic repair of the mounted Caddy storage permissions did not succeed." >&2
        echo "On the host, run for example:" >&2
        echo "  sudo setfacl -R -m u:${APP_UID}:rwx /var/lib/caddy/.local/share/caddy" >&2
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