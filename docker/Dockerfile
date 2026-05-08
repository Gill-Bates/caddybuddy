# syntax=docker/dockerfile:1.7

FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# Leverage BuildKit APT caching to speed up builds with native dependencies
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install --no-install-recommends -y build-essential

# Create isolated virtual environment (PEP 668 compliance)
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./requirements.txt

# Leverage BuildKit pip caching to avoid re-downloading unchanged packages
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

FROM python:3.13-slim AS runtime

ARG APP_VERSION
ARG GIT_SHA
ARG BUILD_DATE

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000 \
    APP_VERSION="$APP_VERSION" \
    GIT_SHA="$GIT_SHA" \
    BUILD_DATE="$BUILD_DATE"

LABEL org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$GIT_SHA \
      org.opencontainers.image.created=$BUILD_DATE

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl gnupg \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
    && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        -o /etc/apt/sources.list.d/caddy-stable.list \
    && apt-get update \
    && apt-get install --no-install-recommends -y caddy \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# Copy pre-compiled virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

COPY --chown=app:app app ./app
COPY --chown=app:app run.py ./run.py
COPY --chown=app:app VERSION ./VERSION
COPY --chown=app:app BUILD_INFO ./BUILD_INFO
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh \
    && install -d -o app -g app /app/data

# NOTE: No USER directive here - entrypoint runs as root to fix
# volume permissions, then drops to app user before exec.

EXPOSE 8000

# Optimized healthcheck: -S skips site-packages import, -O enables optimizations
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -SO -c "import os, sys, urllib.request; port = os.environ.get('PORT', '8000'); response = urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/health', timeout=3); sys.exit(0 if response.status < 500 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

CMD ["python", "run.py"]