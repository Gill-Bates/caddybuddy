# API Reference

CaddyBuddy generates machine-readable OpenAPI documentation directly from the running application, so the reference always matches the deployed version.

## Interactive documentation

| Path | Description |
| --- | --- |
| `/docs` | Swagger UI — browse and try endpoints from the browser. |
| `/redoc` | ReDoc — a static, read-only rendering of the same schema. |
| `/openapi.json` | Raw OpenAPI 3 schema, for generating clients or importing into API tools. |

```bash
open http://127.0.0.1:8000/docs
```

!!! warning "Unauthenticated schema endpoints"
    `/docs`, `/redoc`, and `/openapi.json` are served without authentication and expose the full route and schema list (but not data). Follow the [deployment rules](security.md#deployment-rules): keep the application port bound to loopback or a private network unless direct exposure is intentional.

## Endpoint groups

| Tag | Prefix | Covers |
| --- | --- | --- |
| `system` | `/api/v1/*` | Health/readiness, build info, dashboard metrics, SSL Labs history and registration, the server-sent events stream. |
| `caddy` | `/api/*` | Caddy Admin API status, onboarding, configuration sync, and site CRUD. |
| `passkeys` | `/api/passkeys/*` | WebAuthn registration and sign-in ceremonies. |

The following API endpoints are unauthenticated by design:

- `GET /api/v1/health` — liveness.
- `GET /api/v1/ready` — readiness.
- `GET /api/v1/build-info` — application version and build metadata.
- `POST /api/passkeys/login/start` and `POST /api/passkeys/login/finish` — the passkey sign-in ceremony.

The health and readiness endpoints are documented separately in [Health Checks](health-checks.md). Every other API endpoint requires an authenticated session (the same cookie used by the web UI); Caddy and site-management endpoints additionally require an administrator account. State-changing browser requests, including the passkey sign-in ceremony, must carry the CSRF token described in [Security](security.md#application-protections). Sensitive endpoints (registration, sync, site mutation, and passkey ceremonies) are rate limited.

There is no separate API key or bearer-token flow — the API is intended for the CaddyBuddy UI itself and for scripts that authenticate the same way a browser session would.
