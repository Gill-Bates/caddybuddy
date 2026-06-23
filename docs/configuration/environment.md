# Environment Variables

Most runtime integration settings are managed in the web UI after onboarding. Environment variables configure process-level behavior and advanced deployment options.

## Common variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `CB_SECRET_KEY` | insecure placeholder | Session signing secret; must be at least 32 characters in production |
| `LOG_LEVEL` | `INFO` | Application log level |
| `TZ` | `UTC` | IANA timezone such as `Europe/Berlin` |
| `HOST` | `0.0.0.0` | Listen address |
| `PORT` | `8000` | Listen port |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Trusted proxy addresses used for forwarded headers |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/caddybuddy.db` | SQLAlchemy database URL |
| `CB_CADDY_CERTIFICATES_PATH` | Caddy default storage path | Certificate directory exposed to CaddyBuddy |

Generate a session secret with:

```bash
head -c 32 /dev/urandom | base64
```

## Session settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `SESSION_HTTPS_ONLY` | `true` | Send the session cookie only over HTTPS |
| `SESSION_SAMESITE` | `lax` | Session cookie SameSite policy |
| `PASSWORD_PEPPER` | unset | Optional additional password secret |

Do not disable HTTPS-only cookies in production.

## Caddy runtime control

| Variable | Default | Purpose |
| --- | --- | --- |
| `CB_CADDY_CONTROL_MODE` | `disabled` | `disabled`, `systemd`, `docker`, or `script` |
| `CB_CADDY_SYSTEMD_UNIT` | `caddy` | systemd unit used for runtime control |
| `CB_CADDY_DOCKER_CONTAINER` | `caddy` | Docker container used for runtime control |
| `CB_CADDY_CONTROL_SCRIPT` | `/app/caddy-control.sh` | Custom control script |
| `CB_CADDY_CONTROL_TIMEOUT_SECONDS` | `30` | Runtime-control timeout |
| `CB_CADDY_RESTART_CONFIRMATION_REQUIRED` | `true` | Require confirmation before restarting Caddy |

## SSL Labs

| Variable | Default | Purpose |
| --- | --- | --- |
| `SSLLABS_API_BASE_URL` | `https://api.ssllabs.com/api/v4` | SSL Labs API endpoint |
| `SSLLABS_TIMEOUT_SECONDS` | `20` | Request timeout |
| `SSLLABS_CACHE_MAX_AGE_HOURS` | `24` | Cached registration-state lifetime |

The registration email and history retention are configured in the web UI.
