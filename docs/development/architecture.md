# Architecture

CaddyBuddy is a server-rendered FastAPI application backed by SQLAlchemy and SQLite.

## Main layers

| Layer | Location | Responsibility |
| --- | --- | --- |
| Configuration | `app/config` | Environment validation, logging, limiter configuration |
| Routers | `app/routers` | HTML pages and JSON endpoints |
| Services | `app/services` | Caddy integration, onboarding, SSL Labs, certificates, events |
| Repositories | `app/repositories` | Database access |
| Models and schemas | `app/models`, `app/schemas` | Persistence and response contracts |
| Templates and assets | `app/templates`, `app/static` | Server-rendered UI and browser behavior |

## Configuration flow

1. Site and runtime settings are stored in SQLite.
2. CaddyBuddy assembles the baseline, snippets, and enabled site definitions.
3. The candidate Caddyfile is validated.
4. The configuration is loaded through the private Caddy Admin API.
5. The managed Caddyfile and runtime state are reconciled.

## Live updates

The application uses server-sent events to notify authenticated browser sessions about resource changes. Dashboard and related views refresh their data after receiving an event.

## Design boundary

CaddyBuddy manages a single Caddy installation. It is not a multi-node orchestrator and does not replace host-level backup, firewall, monitoring, or secret-management systems.
