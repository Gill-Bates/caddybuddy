# CaddyBuddy

> A lightweight control plane for managing Caddy servers, reusable Caddyfile snippets, sites, deployments, and access control from one web UI.

[![Python 3.13](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)](https://www.docker.com/)
[![SQLite](https://img.shields.io/badge/SQLite-WAL-003B57.svg)](https://www.sqlite.org/)

CaddyBuddy combines a server-rendered FastAPI UI, an async SQLite backend, and secure operational defaults into a compact management application for Caddy-based environments. It is designed for teams that want to track servers, assign site definitions, reuse Caddyfile snippets, deploy configurations, and audit administrative changes without building a larger control plane first.

## Table Of Contents

- [Overview](#overview)
- [Feature Set](#feature-set)
- [How The Caddy Model Works](#how-the-caddy-model-works)
- [Tech Stack](#tech-stack)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API And Realtime](#api-and-realtime)
- [Project Structure](#project-structure)
- [Quality And Tooling](#quality-and-tooling)
- [Deployment Notes](#deployment-notes)

## Overview

CaddyBuddy manages the operational metadata around Caddy, not the entire host system. The application currently includes browser UI flows for:

- Dashboard
- Servers
- Caddyfile
- Sites
- Queue
- Deployments
- API Keys
- Users
- Audit Logs
- Profile

On first startup with an empty database, the application creates an initial admin account. It also exposes a small system API for health, build information, and Server-Sent Events.

## Feature Set

- Manage multiple Caddy admin endpoints, including status tracking and connectivity checks.
- Store reusable Caddyfile snippets and attach them to sites.
- Model sites as domain plus upstream plus reusable Caddyfile content.
- Render and deploy site configurations to registered Caddy servers.
- Track deployment history and deployment state transitions.
- Manage local users, roles, sessions, and API keys.
- Record audit logs for authentication and administrative actions.
- Publish realtime resource events over SSE at `/api/v1/events`.
- Enforce secure defaults for session handling, CSRF checks, rate limits, and security headers.
- Embed release metadata from `VERSION`, `BUILD_INFO`, and optional build args.

## How The Caddy Model Works

CaddyBuddy separates global Caddy configuration from per-site configuration.

The global Caddy config remains outside the app. This usually contains:

- the top-level `{ ... }` global options block
- reusable snippets such as `(security_headers)` or `(default_log)`
- the final `import /path/to/sites/*.caddy`

Inside the UI, the `Caddyfile` section stores only the inner directives for a single site. The app combines those directives with the selected site domain and renders the outer site block automatically.

Example UI input:

`Domain`

```text
example.com
```

`Upstream`

```text
127.0.0.1:8080
```

`Caddyfile`

```caddyfile
import security_headers
import default_log

reverse_proxy {{upstream}} {
  transport http {
    keepalive 30s
  }
  header_up Host {host}
  header_up X-Real-IP {remote_host}
  header_up X-Forwarded-For {remote_host}
  header_up X-Forwarded-Proto {scheme}
}

encode gzip zstd

header {
  -Server
  -X-Powered-By
}
```

Rendered result:

```caddyfile
example.com {
  import security_headers
  import default_log

  reverse_proxy 127.0.0.1:8080 {
    transport http {
      keepalive 30s
    }
    header_up Host {host}
    header_up X-Real-IP {remote_host}
    header_up X-Forwarded-For {remote_host}
    header_up X-Forwarded-Proto {scheme}
  }

  encode gzip zstd

  header {
    -Server
    -X-Powered-By
  }
}
```

## Tech Stack

| Area | Technology |
| --- | --- |
| Backend | Python 3.13, FastAPI, Uvicorn |
| Database | SQLite in WAL mode, SQLAlchemy 2.x, aiosqlite |
| Frontend | Jinja2, Bootstrap 5, Vanilla JavaScript |
| Security | SlowAPI, SessionMiddleware, CSRF validation, security headers |
| Realtime | Server-Sent Events |
| Tooling | Docker, Buildx, Playwright-based UI lint |

## Quick Start

### Local Development

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

export CADDYBUDDY_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
export CADDYBUDDY_ADMIN_PASSWORD="LocalDevAdminPassword-Change-Me"
export CADDYBUDDY_RELOAD=true
export CADDYBUDDY_SESSION_HTTPS_ONLY=false
export LOG_LEVEL=DEBUG
export TZ=Europe/Berlin

python run.py
```

The application is available at `http://127.0.0.1:8000` by default.

### Build And Run Locally With Docker

```bash
mkdir -p data
printf '%s\n' "dev" > BUILD_INFO

docker build -f docker/Dockerfile -t caddybuddy:local .

docker run --rm \
  -p 8000:8000 \
  -e CADDYBUDDY_SECRET_KEY="$(head -c 32 /dev/urandom | base64)" \
  -e CADDYBUDDY_ADMIN_PASSWORD="LocalDockerAdminPassword-Change-Me" \
  -e CADDYBUDDY_SESSION_HTTPS_ONLY=false \
  -e TZ=Europe/Berlin \
  -v "$PWD/data:/app/data" \
  caddybuddy:local
```

If you want release metadata in `/api/v1/build-info`, set `VERSION` and `BUILD_INFO` before building.

## Configuration

The most important runtime settings are exposed via environment variables:

| Variable | Example | Description |
| --- | --- | --- |
| `CADDYBUDDY_SECRET_KEY` | Base64 string | Required; signs and protects browser sessions. |
| `CADDYBUDDY_ADMIN_PASSWORD` | `replace-me` | Initial admin password used on first startup of an empty database. |
| `CADDYBUDDY_ALLOW_INSECURE_DEFAULTS` | `true` | Allows disposable local setups to start with insecure defaults intentionally. |
| `CADDYBUDDY_RELOAD` | `true` | Enables Uvicorn reload mode for local development. |
| `CADDYBUDDY_SESSION_HTTPS_ONLY` | `false` | Disables `Secure` cookies for local plain-HTTP testing. |
| `CADDYBUDDY_SESSION_SAMESITE` | `lax` | Sets the session cookie `SameSite` policy. |
| `CADDYBUDDY_PORT` or `PORT` | `8000` | HTTP port for the application. |
| `CADDYBUDDY_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Trusted proxy IPs for forwarded headers. |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/app.db` | Database connection URL. |
| `LOG_LEVEL` | `INFO` | Application logging level. |
| `TZ` | `Europe/Berlin` | IANA timezone name. |

Important runtime notes:

- A strong secret key is required unless insecure defaults are explicitly enabled.
- The default admin password is validated only when an initial admin must actually be created.
- For local HTTP testing, set `CADDYBUDDY_SESSION_HTTPS_ONLY=false`.
- The default SQLite database location is `data/app.db`.

## API And Realtime

The system API is intentionally small and operationally focused:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/health` | Health status, app name, and version |
| `GET /api/v1/build-info` | Version, commit, and build date |
| `GET /api/v1/events` | Server-Sent Events stream for resource updates |
| `GET /api/v1/queue/count` | Count of sites pending deployment for authenticated users |

Example:

```bash
curl http://127.0.0.1:8000/api/v1/health
```

## Project Structure

```text
caddybuddy/
├── app/
│   ├── config/            # settings, limiter, logging
│   ├── database/          # engine, sessions, schema init
│   ├── dependencies/      # request helpers, CSRF, session helpers
│   ├── middleware/        # security middleware
│   ├── models/            # SQLAlchemy entities
│   ├── repositories/      # database access layer
│   ├── routers/
│   │   ├── api.py         # system API
│   │   └── ui/            # browser UI routes by area
│   ├── schemas/           # Pydantic models
│   ├── services/          # auth, caddy, deployments, events, build info
│   ├── static/            # CSS, JS, images, vendor assets
│   ├── templates/         # Jinja2 templates
│   └── utils/             # helpers, parsing, banner, caddyfile utils
├── data/                  # runtime data, including SQLite
├── docker/                # Dockerfile and compose example
├── tests/                 # focused regression tests
├── tools/ui-lint/         # browser-based UI auditing
├── VERSION                # release version
├── BUILD_INFO             # commit/build metadata
└── run.py                 # Uvicorn entrypoint
```

## Quality And Tooling

### UI Lint

```bash
cd tools/ui-lint
npm install
npm run install:browsers

UI_LINT_BASE_URL=http://127.0.0.1:8000 \
UI_LINT_USERNAME=admin \
UI_LINT_PASSWORD=admin \
npm run audit
```

The UI lint checks layout, accessibility, interaction quality, and browser-facing regressions.

### Quick Python Syntax Check

```bash
python -m py_compile run.py app/main.py
```

### Focused Tests

```bash
python -m unittest tests.test_database_session tests.test_config_renderer
```

## Deployment Notes

- Run CaddyBuddy behind HTTPS in production.
- Mount `data/` persistently.
- The example `docker/docker-compose.yml` is project-specific infrastructure, not a generic production template.
- For release builds, set `VERSION`, `BUILD_INFO`, and optionally build args such as `APP_VERSION`, `GIT_SHA`, and `BUILD_DATE` deliberately.
- On first startup with an empty database, change the configured initial admin password immediately.
- Deployments currently require a `caddy` binary available in the application runtime, because CaddyBuddy adapts rendered Caddyfile content before sending JSON to the Caddy admin API.

---

CaddyBuddy is intentionally small: a focused Python application for teams that want auditable Caddy operations without adopting a much heavier platform.
