# syntax=docker/dockerfile:1.7

# One base for every stage: builder and runtime must never drift onto
# different Debian releases, since the builder compiles native extensions
# that the runtime stage only copies over as a pre-built venv - it never
# rebuilds them against whatever glibc the runtime image happens to carry.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Debian's default apt post-invoke hook deletes downloaded .deb files after
# every install, which would empty the BuildKit cache mount below before it
# ever pays off across builds.
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

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

# pyproject.toml is the single dependency manifest; constraints.txt is
# optional and never committed. Nothing in pyproject.toml is pinned, so an
# amd64 and an arm64 build started minutes apart could otherwise resolve
# different versions; constraints.txt - resolved once per release for both
# target platforms by the release workflow - is what keeps them identical.
# A plain local `docker build` has no such file and just resolves the
# newest set itself - though an incremental build only does that when this
# layer actually re-runs; use `--pull --no-cache` for a guaranteed-fresh
# resolution.
COPY pyproject.toml constraints*.txt ./

# Leverage BuildKit pip caching to avoid re-downloading unchanged packages
RUN --mount=type=cache,target=/root/.cache/pip \
    set -euo pipefail; \
    python -c 'import tomllib; print("\n".join(tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]))' > /tmp/requirements.txt; \
    pinned=(); \
    if [ -f constraints.txt ]; then \
        echo "Installing against the versions resolved once for this release:"; \
        cat constraints.txt; \
        pinned=(--constraint constraints.txt); \
    fi; \
    python -m pip install "${pinned[@]}" -r /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt

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

# Copy pre-compiled virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Application code stays root-owned and world-readable, never writable by
# the app user it runs as - only /app/data (below) needs to be. A process
# that can write into its own import path can plant a module there for the
# next privileged reader to import; see the HEALTHCHECK below for why that
# matters here specifically.
COPY app ./app
COPY run.py ./run.py
COPY pyproject.toml ./pyproject.toml
COPY BUILD_INFO ./BUILD_INFO
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN python -m compileall -q app run.py \
    && install -d -o app -g app -m 0700 /app/data

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

# Runs as the same uid the main process drops to (not root, see ENTRYPOINT):
# /app is root-owned and app-readable, but python -c resolves an empty
# sys.path entry to the current directory, and WORKDIR /app would otherwise
# hand a root-run import a directory the app user could plant files in.
# -I additionally strips PYTHONPATH/user-site and (implied -P) the CWD from
# sys.path; ProxyHandler({}) bypasses any HTTP_PROXY/HTTPS_PROXY set for
# unrelated reasons so the loopback probe can't be redirected through one.
# A response is only reached for a 2xx status; urlopen raises for 4xx/5xx.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD gosu 1000:1000 python -I -c "\
import os, sys, urllib.request as u; \
port = os.environ.get('PORT', '8000'); \
opener = u.build_opener(u.ProxyHandler({})); \
response = opener.open(f'http://127.0.0.1:{port}/api/v1/health', timeout=3); \
sys.exit(0 if response.status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

CMD ["python", "run.py"]
