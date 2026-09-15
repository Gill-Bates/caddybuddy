<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# routers

## Purpose
HTTP routing layer. Contains the JSON API (`api.py`, `caddy_api.py`) and, in the `ui/` subpackage, the server-rendered HTML application.

## Key Files
| File | Description |
|------|-------------|
| `api.py` | General JSON API: `/health`, `/ready`, `/build-info`, `/caddy/status`, `/dashboard/metrics`, `/dashboard/ssllabs-history`, `/events` (Server-Sent Events stream via `_event_stream`), and SSL Labs registration endpoints (`/ssllabs/registration-status`, `/ssllabs/register`, `/ssllabs/refresh-status`). |
| `caddy_api.py` | Caddy-management JSON API: `/caddy/onboard`, `/caddy/sync`, and site CRUD (`/sites` GET/POST, `/sites/{id}` PATCH/DELETE), including admin-auth checks (`_require_admin_api_user`) and post-mutation sync/event publishing. |
| `__init__.py` | Package marker. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `ui/` | Server-rendered HTML pages (login, dashboard, onboarding wizard, settings, sites, Caddyfile editor, SSL Labs). See `ui/AGENTS.md`. |

## For AI Agents

### Working In This Directory
- `api.py`/`caddy_api.py` return Pydantic models from `app/schemas/` — don't return raw dicts/ORM entities from these endpoints.
- Admin-gated endpoints use `_require_admin_api_user`/`_require_api_user` helpers defined locally in each file — reuse them rather than re-implementing auth checks.
- `/events` (SSE) is powered by `app/services/events.py`'s in-memory event bus (`_event_stream`) — it is single-process; be aware of this if changing deployment to multiple worker processes.

### Testing Requirements
- See `../../tests/test_api_router.py`, `test_caddy_api.py`, `test_caddy_api_sites.py`.

### Common Patterns
- FastAPI `APIRouter` per module; `response_model=` on every route for schema enforcement; module-local private helpers prefixed `_`.

## Dependencies

### Internal
- `app/services/*` (business logic), `app/schemas/*` (response models), `app/dependencies/web.py` (session/auth helpers).

### External
- `fastapi`, `pydantic` (`BaseModel` for locally-defined response models like `SslLabsRegistrationStatusResponse`).

<!-- MANUAL: -->
