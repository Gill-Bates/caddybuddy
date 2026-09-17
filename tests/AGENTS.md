<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# tests

## Purpose
Python test suite for the `app/` package, plus standalone Node (`.mjs`) tests for pure frontend logic and Docker/tooling-workflow tests.

## Key Files
| File | Description |
|------|-------------|
| `ui_test_app.py` | Shared test-fixture FastAPI app/harness used by the `test_ui_*.py` files to exercise UI routes without the full production app wiring; also `extract_csrf_token()` for pulling the form token from rendered HTML. |
| `env_overrides.py` | `TEST_ENV` (standard secret/admin-password overrides) and `ModuleEnv`: construct at module level before app imports, call `.apply()` in `setUp` if needed and `.restore()` in `tearDownModule`. |
| `test_caddy_onboarding.py`, `test_caddy_onboarding_flow.py` | Cover `app/services/caddy_onboarding.py`. |
| `test_ssllabs_service.py` | Covers `app/services/ssllabs.py`. |
| `test_ui_sites.py`, `test_ui_onboarding.py`, `test_ui_settings.py`, `test_ui_ssllabs.py`, `test_ui_caddyfile.py`, `test_ui_dashboard.py`, `test_ui_auth.py`, `test_ui_about.py`, `test_ui_common.py`, `test_ui_toasts.py` | Cover `app/routers/ui/*` and `app/routers/ui/_common.py`, one file per router module. |
| `test_api_router.py`, `test_caddy_api.py`, `test_caddy_api_sites.py` | Cover `app/routers/api.py` and `app/routers/caddy_api.py`. |
| `test_database_session.py` | Covers `app/database/session.py` locking and session lifecycle. |
| `test_dockerfile.py`, `test_docker_workflow.py`, `test_entrypoint.py` | Assert on `../docker/` content/structure (not a live Docker build). |
| `test_docs_workflow.py` | Asserts on `../docs/` / `../mkdocs.yml` workflow expectations. |
| `test_settings_retention.mjs`, `test_ssllabs_history_chart.mjs`, `test_caddy_editor_braces.mjs` | Standalone Node tests for pure JS logic (settings retention, dashboard SSL Labs history chart, Caddyfile editor brace scanner) — run with Node directly, not via `pytest` or `tools/ui-lint/`. |

## For AI Agents

### Working In This Directory
- Tests are organized by source module or behavior. Extend the closest focused test file; add a new file when the concern is genuinely separate (UI routes commonly use `test_ui_<module>.py`).
- `ui_test_app.py` is infrastructure, not a test file itself — read it before adding a new `test_ui_*.py` file so fixtures/auth bypass are reused consistently.
- The `.mjs` files here are independent of `tools/ui-lint/`'s Playwright suite — they're plain Node assertions on JS logic, not browser tests.

### Testing Requirements
- Run the Python suite with `.venv/bin/python -m pytest` from the repo root and lint with `.venv/bin/ruff check .`.
- Run the `.mjs` files from the repo root, either individually (`node tests/test_settings_retention.mjs`) or together (`node --test tests/*.mjs`). Some of them read source files via repo-relative paths, so the repo root is the required working directory.

### Common Patterns
- Async tests primarily use `unittest.IsolatedAsyncioTestCase`; follow the neighboring file's established harness.
- Prefer extending an existing focused test file unless testing a genuinely separate concern.

## Dependencies

### Internal
- Exercises `app/` end-to-end via `ui_test_app.py` and direct unit tests against services/repositories/utils.

### External
- `pytest` (and async test support), `httpx` (test client), Node.js for the `.mjs` files.

<!-- MANUAL: -->
