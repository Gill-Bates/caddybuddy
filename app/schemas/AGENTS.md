<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# schemas

## Purpose
Pydantic request/response models for the JSON API (`app/routers/api.py`, `app/routers/caddy_api.py`). Kept separate from `app/models/` (SQLAlchemy ORM entities) so wire format can evolve independently of storage schema.

## Key Files
| File | Description |
|------|-------------|
| `caddy.py` | `CaddyStatusResponse`, `CaddyOnboardResponse`, `CaddySyncResponse`, `SiteResponse`, `SiteCreateRequest`, `SiteUpdateRequest`, `SiteMutationResponse`, `SiteDeleteResponse` — the Caddy/site management API contract. |
| `system.py` | `HealthResponse`, `BuildInfoResponse`, `CaddyStatusResponse` (system variant), `DashboardMetricsResponse`, `SslLabsRankPointResponse`/`SslLabsRankSeriesResponse`/`SslLabsRankHistoryResponse` — health/observability/dashboard API contract. |
| `ssllabs.py` | SSL Labs-related request/response models. |
| `__init__.py` | Package marker. |

## For AI Agents

### Working In This Directory
- Note there are two distinctly-named `CaddyStatusResponse` classes (one in `caddy.py`, one in `system.py`) — check imports carefully when editing either; they serve different endpoints (`caddy_api.py` vs `api.py`).
- Changing a field here is a wire-format/API-contract change — check both the router that uses it and any JS/template consumers (e.g. dashboard chart data) before renaming or removing fields.

### Testing Requirements
- Exercised via the router tests (`tests/test_api_router.py`, `test_caddy_api.py`, `test_caddy_api_sites.py`) rather than standalone schema tests.

### Common Patterns
- Plain `pydantic.BaseModel` subclasses, typically with `response_model=` wired up in the corresponding router.

## Dependencies

### Internal
- Consumed by `app/routers/api.py` and `app/routers/caddy_api.py`.

### External
- `pydantic`.

<!-- MANUAL: -->
