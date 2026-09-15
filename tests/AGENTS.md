<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# tests

## Purpose
Pytest suite for the `app/` package, one `test_<module>.py` file per source module (matching `app/`'s layering: config, database, dependencies, middleware, models, repositories, routers, services, utils), plus a small number of standalone Node (`.mjs`) tests for pure frontend logic and Docker/tooling-workflow tests.

## Key Files
| File | Description |
|------|-------------|
| `ui_test_app.py` | Shared test-fixture FastAPI app/harness used by the `test_ui_*.py` files to exercise UI routes without the full production app wiring. |
| `test_caddy_onboarding.py`, `test_caddy_onboarding_flow.py` | The two largest test files in the repo (69KB / 39KB) — cover `app/services/caddy_onboarding.py`, a hot-path module. |
| `test_ssllabs_service.py` | Largest single test file (43KB) — covers `app/services/ssllabs.py`, another hot-path module. |
| `test_ui_sites.py`, `test_ui_onboarding.py`, `test_ui_settings.py`, `test_ui_ssllabs.py`, `test_ui_caddyfile.py`, `test_ui_dashboard.py`, `test_ui_auth.py`, `test_ui_about.py`, `test_ui_common.py`, `test_ui_toasts.py` | Cover `app/routers/ui/*` and `app/routers/ui/_common.py`, one file per router module. |
| `test_api_router.py`, `test_caddy_api.py`, `test_caddy_api_sites.py` | Cover `app/routers/api.py` and `app/routers/caddy_api.py`. |
| `test_database_session.py` | Large (25KB) — covers `app/database/session.py`'s locking/session-lifecycle behavior. |
| `test_dockerfile.py`, `test_docker_workflow.py`, `test_entrypoint.py` | Assert on `../docker/` content/structure (not a live Docker build). |
| `test_docs_workflow.py` | Asserts on `../docs/` / `../mkdocs.yml` workflow expectations. |
| `test_settings_retention.mjs`, `test_ssllabs_history_chart.mjs` | Standalone Node tests for pure JS logic (settings retention, dashboard SSL Labs history chart) — run with Node directly, not via `pytest` or `tools/ui-lint/`. |

## For AI Agents

### Working In This Directory
- Naming convention is strict: a change to `app/<subpkg>/<module>.py` should have a matching change in `tests/test_<module>.py` (or `test_<subpkg>_<module>.py` for UI routes, e.g. `app/routers/ui/sites.py` → `test_ui_sites.py`). Follow this when adding new modules.
- `ui_test_app.py` is infrastructure, not a test file itself — read it before adding a new `test_ui_*.py` file so fixtures/auth bypass are reused consistently.
- The two `.mjs` files here are independent of `tools/ui-lint/`'s Playwright suite — they're plain Node assertions on JS logic, not browser tests.

### Testing Requirements
- Run the Python suite with `pytest` from the repo root; `ruff check` for lint.
- Run the `.mjs` files directly with `node tests/test_settings_retention.mjs` (etc.) — check the file header/CI workflow for exact invocation if adding a new one.

### Common Patterns
- Async test functions (`pytest-asyncio`-style) for anything touching the async DB/session or async services.
- Given the many hot-path service tests (onboarding, SSL Labs), prefer extending the existing large test file over creating a new one unless testing a genuinely separate concern.

## Dependencies

### Internal
- Exercises `app/` end-to-end via `ui_test_app.py` and direct unit tests against services/repositories/utils.

### External
- `pytest` (and async test support), `httpx` (test client), Node.js for the `.mjs` files.

<!-- MANUAL: -->
