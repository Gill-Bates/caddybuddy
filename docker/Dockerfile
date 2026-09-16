# syntax=docker/dockerfile:1.7

# The stages begin from the same refreshed base image. Builder-only packages
# are installed separately, so this does not promise an identical final
# package set between builder and runtime.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Debian's default apt post-invoke hook deletes downloaded .deb files after
# every install, which would empty the BuildKit cache mount below before it
# ever pays off across builds.
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

# A release build uses --pull --no-cache so this upgrade is evaluated against
# the current Debian archive rather than a cached layer.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends

FROM base AS builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /build

# Leverage BuildKit APT caching to speed up builds with native dependencies.
# /var/cache/apt and /var/lib/apt are cache mounts, not image layers, so
# nothing here needs (or benefits from) an `rm -rf .../lists/*` afterwards.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install --no-install-recommends -y build-essential

# Create isolated virtual environment, so it can be copied into the runtime
# stage as one self-contained directory.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip setuptools wheel

# Dependencies are deliberately unpinned. Each fresh build resolves the
# newest compatible package set for its target platform.
COPY pyproject.toml ./

# Leverage BuildKit pip caching to avoid re-downloading unchanged packages
RUN --mount=type=cache,target=/root/.cache/pip \
    set -euo pipefail; \
    python -c 'import tomllib; print("\n".join(tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]))' > /tmp/requirements.txt; \
    python -m pip install -r /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt; \
    python -m pip check \
    && python -m pip uninstall -y --root-user-action=ignore pip

FROM base AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PORT=8000

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install --no-install-recommends -y acl ca-certificates gosu

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# Copy the checked, pre-compiled virtual environment from builder. pip was
# removed in the builder, so it does not add a redundant runtime layer.
COPY --from=builder /opt/venv /opt/venv

# Application code stays root-owned and world-readable, never writable by
# the app user it runs as - only /app/data (below) needs to be. A process
# that can write into its own import path can plant a module there for the
# next privileged reader to import; see the HEALTHCHECK below for why that
# matters here specifically.
# Docker preserves build-context modes by default. Git only records the
# executable bit, so a host file made 0600 can otherwise enter the image
# unreadable to the unprivileged app process. Normalize copied files and
# directories explicitly: applying COPY --chmod=0644 to a directory tree
# would remove its traversal bit too.
COPY app ./app
COPY run.py ./run.py
COPY pyproject.toml ./pyproject.toml
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN find app -type d -exec chmod 0755 {} + \
    && find app -type f -exec chmod 0644 {} + \
    && chmod 0644 run.py pyproject.toml \
    && python -m compileall -q app run.py \
    && install -d -o app -g app -m 0700 /app/data

# Build metadata and the changelog change for every release but are not needed
# to compile the application, so copying them afterwards preserves the
# application layer. CHANGELOG.md is a runtime asset, not just documentation:
# the About page reads it from base_dir (/app) and renders it as sanitized HTML,
# so an image without it shows "Changelog not found".
COPY --chmod=0644 BUILD_INFO ./BUILD_INFO
COPY --chmod=0644 CHANGELOG.md ./CHANGELOG.md

# NOTE: No USER directive here - entrypoint runs as root to fix
# volume permissions, then drops to app user before exec.

# Build metadata last: GIT_SHA/BUILD_DATE/APP_VERSION are unique to every
# build, so anything placed after them would lose its layer cache on every
# single build. Nothing below this point depends on them except the label.
ARG APP_VERSION=dev
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown

ENV GIT_SHA="$GIT_SHA" \
    BUILD_DATE="$BUILD_DATE"

LABEL org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$GIT_SHA \
      org.opencontainers.image.created=$BUILD_DATE

EXPOSE 8000

# Runs as the same uid the main process drops to (not root, see ENTRYPOINT).
# -I strips PYTHONPATH/user-site and the CWD from sys.path. HTTPConnection
# connects directly to loopback, never follows redirects, and requires 200.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD gosu 1000:1000 python -I -c "\
import http.client as h, os, sys; \
port = os.environ.get('PORT', '8000'); \
connection = h.HTTPConnection('127.0.0.1', int(port), timeout=3); \
connection.request('GET', '/api/v1/health'); \
response = connection.getresponse(); \
sys.exit(0 if response.status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

CMD ["python", "run.py"]
