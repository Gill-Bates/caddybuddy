<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# routers

## Purpose
HTTP routing layer. Contains the JSON API (`api.py`, `caddy_api.py`) and, in the `ui/` subpackage, the server-rendered HTML application.

## Key Files
| File | Description |
|------|-------------|
| `api.py` | General JSON API: `/health`, `/ready`, `/build-info`, `/caddy/status`, `/dashboard/metrics`, `/dashboard/ssllabs-history`, `/events` (Server-Sent Events stream via `_event_stream`), and SSL Labs registration endpoints (`/ssllabs/registration-status`, `/ssllabs/register`, `/ssllabs/refresh-status`). |
| `caddy_api.py` | Caddy-management JSON API: `/caddy/onboard`, `/caddy/sync`, and site CRUD (`/sites` GET/POST, `/sites/{id}` PATCH/DELETE), gated by `require_admin_api_user` and followed by post-mutation sync/event publishing. |
| `__init__.py` | Package marker. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `ui/` | Server-rendered HTML pages (login, dashboard, onboarding wizard, settings, sites, Caddyfile editor, SSL Labs). See `ui/AGENTS.md`. |

## For AI Agents

### Working In This Directory
- JSON endpoints should use Pydantic response contracts from `app/schemas/` or a small route-local model. Streaming, file, redirect, and empty responses are explicit exceptions; never serialize ORM entities directly.
- Auth-gated JSON endpoints depend on `require_api_user`/`require_admin_api_user` from `app/dependencies/web.py` (401 when anonymous, 403 when not an admin). Reuse those dependencies rather than re-implementing the check per module.
- `/events` (SSE) is powered by `app/services/events.py`'s in-memory event bus (`_event_stream`) — it is single-process; be aware of this if changing deployment to multiple worker processes.

### Testing Requirements
- See `../../tests/test_api_router.py`, `test_caddy_api.py`, `test_caddy_api_sites.py`.

### Common Patterns
- FastAPI `APIRouter` per module; use `response_model=` for JSON routes where FastAPI can enforce a schema, while streaming and other non-JSON responses use an explicit response class; module-local private helpers are prefixed `_`.

## Dependencies

### Internal
- `app/services/*` (business logic), `app/schemas/*` (response models), `app/dependencies/web.py` (session/auth helpers).

### External
- `fastapi`, `pydantic` (`BaseModel` for locally-defined response models like `SslLabsRegistrationStatusResponse`).

<!-- MANUAL: -->
