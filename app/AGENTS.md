<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# app

## Purpose
The CaddyBuddy FastAPI application package — the only package actually distributed by the project (see the root `pyproject.toml`). Composes configuration, database access, HTTP routing (both a JSON API and server-rendered UI), business-logic services, and static assets/templates into one ASGI app.

## Key Files
| File | Description |
|------|-------------|
| `main.py` | Application factory: FastAPI app construction, lifespan/startup (DB init, banner, background tasks), middleware registration, static/template mounting, global exception handling. |
| `__init__.py` | Package marker (`"""CaddyBuddy application package."""`). |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `config/` | Settings (pydantic-settings), rate limiter, logging config. See `config/AGENTS.md`. |
| `database/` | Async SQLAlchemy engine/session management for the SQLite backend. See `database/AGENTS.md`. |
| `dependencies/` | FastAPI `Depends()` helpers shared across web routes (auth, CSRF, flashes, asset integrity). See `dependencies/AGENTS.md`. |
| `middleware/` | ASGI middleware: CSRF protection and session/cookie handling. See `middleware/AGENTS.md`. |
| `models/` | SQLAlchemy ORM entities. See `models/AGENTS.md`. |
| `repositories/` | DB access layer, one repository per aggregate (users, sites, SSL Labs data, app settings). See `repositories/AGENTS.md`. |
| `routers/` | HTTP layer: `api.py`/`caddy_api.py` (JSON API) plus the `ui/` subpackage (server-rendered pages). See `routers/AGENTS.md`. |
| `schemas/` | Pydantic request/response models for the JSON API. See `schemas/AGENTS.md`. |
| `services/` | Business logic: Caddy control, onboarding wizard, certificates, SSL Labs scanning, dashboard aggregation, etc. See `services/AGENTS.md`. |
| `static/` | CSS/JS/image assets, including vendored/bundled third-party JS. See `static/AGENTS.md`. |
| `templates/` | Jinja2 templates for the server-rendered UI. See `templates/AGENTS.md`. |
| `utils/` | Stateless helper modules (Caddyfile parsing, domain validation, security logging, anti-bot, admin-API targeting policy). See `utils/AGENTS.md`. |

## For AI Agents

### Working In This Directory
- Prefer `routers` → `services` → `repositories` for non-trivial workflows. Existing routers may use repositories for simple lookups, and transaction-bound services such as `caddyfile_manager.py` may perform tightly coupled ORM work. Do not add raw SQLAlchemy queries to routers.
- `routers/api.py` + `routers/caddy_api.py` are the JSON API; `routers/ui/*` are server-rendered HTML pages. Keep shared/stable JSON contracts in `schemas/`; small route-local models and streaming responses are established exceptions.
- Keep asynchronous I/O paths async. Pure parsing/formatting helpers may be synchronous, and blocking filesystem work should use the existing offload patterns.

### Testing Requirements
- Corresponding tests live under `../tests/` (e.g. `app/services/ssllabs.py` ↔ `tests/test_ssllabs_service.py`). When behavior changes, extend the closest relevant tests; documentation-only or structural edits do not require artificial test changes.
- Run `.venv/bin/python -m pytest` from the repo root and `.venv/bin/ruff check .` for lint.

### Common Patterns
- Settings are read via `app.config.settings.get_settings()` (cached) rather than reading env vars directly outside `config/`.
- DB sessions are obtained via the `app.dependencies.web.get_db_session` / `app.database.session` machinery, not constructed ad hoc.
- Flash messages, CSRF tokens, and CSP nonces for UI pages go through `app.dependencies.web` helpers.

## Dependencies

### Internal
- All subpackages listed above interlink per the layering described; `main.py` is the composition root that wires them together.

### External
- `fastapi`, `sqlalchemy`, `aiosqlite`, `pydantic`/`pydantic-settings`, `jinja2`, `slowapi`, `itsdangerous`, `bcrypt`, `cryptography`, `nh3`, `httpx`.

<!-- MANUAL: -->
