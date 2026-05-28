<p align="center">
   <img src="app/static/img/caddybuddy_1c.svg" width="400" alt="CaddyBuddy logo">
</p>

<h2 align="center">Manage your Caddy servers with ease!</h2>

<p align="center">
  <a href="https://github.com/Gill-Bates/caddybuddy/releases"><img src="https://img.shields.io/github/v/release/Gill-Bates/caddybuddy?logo=github&logoColor=white" alt="GitHub Release"></a>
  <a href="https://hub.docker.com/r/giiibates/caddybuddy"><img src="https://img.shields.io/docker/pulls/giiibates/caddybuddy?logo=docker&logoColor=white" alt="Docker Pulls"></a>
  <a href="https://hub.docker.com/r/giiibates/caddybuddy"><img src="https://img.shields.io/docker/image-size/giiibates/caddybuddy?logo=docker&logoColor=white" alt="Docker Image Size"></a>
  <br>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg" alt="License"></a>
  <a href="#overview"><img src="https://img.shields.io/badge/Docs-README-green?logo=readthedocs&logoColor=white" alt="Documentation"></a>
  <img src="https://img.shields.io/badge/Platform-linux%2Famd64%20|%20linux%2Farm64-lightgrey?logo=linux&logoColor=white" alt="Platform">
</p>

<p align="center">
   <a href="#overview">📚 Documentation</a> •
   <a href="#quick-start">🚀 Quick Start</a> •
  <a href="CHANGELOG.md">📋 Changelog</a>
</p>

> A lightweight control plane for managing Caddy servers, reusable Caddyfile snippets, sites, deployments, and access control from one web UI.

## Overview

CaddyBuddy combines a server-rendered FastAPI UI, SQLite, and secure defaults into a compact control plane for a single Caddy installation. This README focuses on one thing: getting the application up and running quickly.

## Features

- **Dashboard** — Real-time Caddy status, version info, uptime, and certificate metrics at a glance
- **Sites Management** — Create, edit, and deploy site configurations with domain validation
- **Caddyfile Editor** — Edit the global Caddyfile with syntax validation before deployment
- **Certificate Monitoring** — Track SSL certificate validity and expiration dates
- **SSL Labs Integration** — Schedule and view Qualys SSL Labs assessments for your domains
- **Onboarding Wizard** — Guided setup to initialize CaddyBuddy with your existing Caddyfile
- **Dark Mode** — Full light/dark theme support
- **Secure by Default** — CSRF protection, security headers, rate limiting, and session management

## What You Need

- Docker and Docker Compose for the recommended path
- Or Python 3.13 if you want to run it locally without containers
- A strong random session secret (`CB_SECRET_KEY`)

## Quick Start

### Docker Compose

The repository ships an example Compose file at `docker/docker-compose.yml.example`.

Release images are built for `giiibates/caddybuddy` on Docker Hub.

1. Create a persistent data directory:

   ```bash
   mkdir -p data
   ```

2. Make sure the Caddy Admin API is reachable from the container.

   Do not expose the Caddy Admin API publicly. Bind it to localhost, a private Docker network, or another trusted internal address only.

   The example Compose file already defines `host.docker.internal` via `host-gateway` for Linux hosts.

3. If you want to import an existing Caddyfile during onboarding, make sure the host file already exists and is writable:

   ```bash
   ls -l /etc/caddy/Caddyfile
   ```

4. Export the required variable:

   ```bash
   export CB_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
   ```

5. Start the container:

   ```bash
   docker compose -f docker/docker-compose.yml.example up -d
   ```

6. Open the UI:

   ```text
   http://127.0.0.1:8000
   ```

### Local Development

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

export CB_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
export LOG_LEVEL="DEBUG"

python run.py
```

The local server listens on `http://127.0.0.1:8000` by default.

## First Startup Behavior

- On an empty database, CaddyBuddy creates the initial `admin` user with the default password `admin`.
- The SQLite database lives in `data/app.db` by default.
- Caddy Admin URL, Caddyfile path, and rate limiting are configured in the Settings page after login.
- Without Caddy onboarding, the application reports that onboarding is required and you can trigger it from the UI or API.

Change the initial admin password immediately after the first login.

## Environment Variables

| Variable | Meaning |
| --- | --- |
| `CB_SECRET_KEY` | Required session secret (generate with `head -c 32 /dev/urandom \| base64`) |
| `LOG_LEVEL` | Application log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`; default: `INFO`) |
| `TZ` | Container or process timezone (e.g., `Europe/Berlin`) |

All other settings (Caddy Admin URL, Caddyfile path, rate limiting) are managed in the web UI under Settings.

## Health And Readiness

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/ready
```

`/health` checks whether the web process is alive. `/ready` also verifies whether Caddy onboarding is complete and the Caddy runtime integration is usable.

## Operational Notes

- Run CaddyBuddy behind HTTPS in production.
- Persist the `data/` directory.
- Do not expose the Caddy Admin API publicly.
- The mounted Caddyfile must exist and be writable before the first container start if you want onboarding import.
- CaddyBuddy manages application state and generated Caddy configuration; it does not replace the rest of your host setup.

## Reverse Proxy Configuration

When running CaddyBuddy behind a reverse proxy (like Caddy itself), ensure the proxy passes the correct headers:

```caddyfile
caddy.example.com {
    reverse_proxy 127.0.0.1:8000 {
        header_up Host {host}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

The `X-Forwarded-Proto` header is required for CSRF protection to work correctly over HTTPS.

## Development

### UI Lint

CaddyBuddy includes a Playwright-based UI lint tool for visual regression testing and accessibility checks:

```bash
cd tools/ui-lint
npm install
npx playwright install chromium firefox webkit

UI_LINT_BASE_URL=http://localhost:8000 \
UI_LINT_USERNAME=admin \
UI_LINT_PASSWORD=admin \
npm run audit
```

Results are saved to `tools/ui-lint/test-results/`.

## License

[AGPL-3.0](LICENSE)
