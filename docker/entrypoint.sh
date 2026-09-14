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
    mkdir -p "$DATA_PATH"

    # Repair only entries that do not already have the right owner, rather
    # than gate one recursive chown on the top directory's owner. The top
    # directory's owner says nothing about its content: a volume re-mounted
    # from elsewhere can have a correctly-owned top directory over files
    # left behind with a different owner underneath.
    find "$DATA_PATH" \( ! -uid "$APP_UID" -o ! -gid "$APP_GID" \) \
        -exec chown "${APP_UID}:${APP_GID}" '{}' +
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
    local segment_count
    local acl_failures=0
    local current
    local sample_cert

    case "$cert_dir" in
        /*) ;;
        *)
            echo "ERROR: CB_CADDY_CERTIFICATES_PATH must be an absolute path: $cert_dir" >&2
            exit 1
            ;;
    esac

    # Canonicalize before any further checks: '..' segments, repeated
    # slashes, and symlinks must not let an unsafe-looking path resolve to
    # something dangerous (e.g. "/var/.." resolves to "/", which the old
    # check - comparing the raw string to "/" - would not have caught).
    # readlink -f resolves whatever already exists and appends any
    # not-yet-created tail unchanged, so this works before mkdir too.
    cert_dir="$(readlink -f -- "$cert_dir" 2>/dev/null || true)"
    if [ -z "$cert_dir" ]; then
        echo "ERROR: could not resolve CB_CADDY_CERTIFICATES_PATH" >&2
        exit 1
    fi

    segment_count="$(printf '%s' "$cert_dir" | tr -s '/' '\n' | grep -c .)"
    if [ "$cert_dir" = "/" ] || [ "$segment_count" -lt 2 ]; then
        echo "ERROR: refusing unsafe certificate storage path: $cert_dir" >&2
        echo "Use a path at least two levels below root, e.g. /data/caddy-certificates." >&2
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
            setfacl -m "u:${APP_UID}:x" "$current" \
                || echo "WARNING: could not grant traversal on $current" >&2
        fi
        current="$(dirname "$current")"
    done

    # Existing tree: directories get list+traverse, plus a default ACL (with
    # an explicit mask) so issuer/domain directories Caddy creates later
    # inherit the same access without this script having to run again.
    # CaddyBuddy only ever parses *.crt files (see app/services/certificates.py)
    # to read expiry/SAN/issuer metadata - it never opens the private keys or
    # ACME account metadata (*.key, *.json) stored alongside them, so only
    # *.crt files are granted read access.
    #
    # Residual limitation: a POSIX default ACL sets the *initial* ACL of a
    # new file, but a later chmod recomputes that file's ACL mask from its
    # own mode bits and can zero out the granted access again (verified:
    # chmod 600 on an inherited-ACL file leaves the named-user entry with
    # effective "---"). Caddy typically chmods private keys this way, which
    # only reinforces why they are excluded above; if *.crt files themselves
    # were ever written with restrictive permissions, this default ACL alone
    # would not survive that either, and re-running this routine after a
    # renewal would be the only fix.
    find "$cert_dir" -type d -exec setfacl -m "u:${APP_UID}:rx" -d -m "u:${APP_UID}:rx,mask::rwx" '{}' + \
        || acl_failures=$((acl_failures + 1))
    find "$cert_dir" -type f -name '*.crt' -exec setfacl -m "u:${APP_UID}:r" '{}' + \
        || acl_failures=$((acl_failures + 1))

    if [ "$acl_failures" -gt 0 ]; then
        echo "WARNING: ${acl_failures} certificate ACL operation(s) failed; some certificates may not be readable." >&2
        echo "Check that the certificate storage filesystem supports POSIX ACLs (e.g. mounted with the 'acl' option)." >&2
    fi

    if ! gosu "${APP_UID}:${APP_GID}" test -x "$cert_dir" 2>/dev/null; then
        echo "ERROR: Caddy certificate storage is not traversable by uid=${APP_UID}: $cert_dir" >&2
        echo "Automatic repair of the mounted Caddy storage permissions did not succeed." >&2
        exit 1
    fi

    # A directory being traversable says nothing about whether any actual
    # certificate in it is readable; check one for real when there is one.
    sample_cert="$(find "$cert_dir" -type f -name '*.crt' -print -quit 2>/dev/null || true)"
    if [ -n "$sample_cert" ] && ! gosu "${APP_UID}:${APP_GID}" test -r "$sample_cert" 2>/dev/null; then
        echo "WARNING: certificate storage is traversable but $sample_cert is not readable by uid=${APP_UID}." >&2
        echo "Certificate inspection may be incomplete." >&2
    fi
}

verify_data_permissions() {
    local test_file

    test_file="$(gosu "${APP_UID}:${APP_GID}" mktemp "${DATA_PATH}/.write_test.XXXXXX" 2>/dev/null)" || {
        echo "ERROR: $DATA_PATH is not writable by uid=${APP_UID}." >&2
        echo "Check volume ownership or mount options." >&2
        exit 1
    }

    if ! gosu "${APP_UID}:${APP_GID}" rm -f "$test_file"; then
        echo "ERROR: $DATA_PATH entries are not removable by uid=${APP_UID}." >&2
        echo "Check volume ownership or mount options." >&2
        exit 1
    fi
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

exec_as_app() {
    exec gosu "${APP_UID}:${APP_GID}" /bin/bash -eu -c '
if python -c "from app.utils.banner import print_banner_once; print_banner_once()"; then
    export CADDYBUDDY_BANNER_PRINTED=1
fi
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
