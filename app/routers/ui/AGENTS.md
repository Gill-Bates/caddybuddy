<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# ui

## Purpose
Server-rendered HTML pages — the actual browser-facing application. Each module owns one page (or a small family of related pages/actions) and renders a Jinja2 template from `app/templates/`.

## Key Files
| File | Description |
|------|-------------|
| `_common.py` | Shared helpers used across every UI route: `require_onboarding_completed`, `require_user`/`require_admin` (auth gating that returns `None` instead of raising, so pages can flash/redirect), `validated_csrf_form` (CSRF-checked form parsing), `safe_next`/`parse_int`/`parse_checkbox`, `commit_and_flash` (commit DB changes + push a flash message). |
| `auth.py` | Login, first-admin setup, and logout, including invisible-captcha context (`_captcha_context`) and failure rendering (`_render_login_failure`). OTP-enabled accounts get a session-bound, fingerprinted, 180-second second-factor challenge (`/login/otp`, cancellable via `/login/otp/cancel`); the authenticated session is issued only after the second factor. `/login`, `/login/otp`, and `/setup` are all rate-limited (`5/minute;20/hour`, `5/minute;20/hour`, and `10/minute` respectively). |
| `dashboard.py` | Home/dashboard page (`home_page`). |
| `onboarding.py` | First-run onboarding wizard: location/mode/preflight/enable-admin-API/execute steps. |
| `settings.py` | App settings page: Caddy settings, SSL Labs settings/retention, password change, two-factor enrollment/confirmation/disable (`/settings/two-factor*`, responses with secrets or recovery codes are `Cache-Control: no-store`), SSL Labs email registration; also `restart_onboarding_wizard`. |
| `sites.py` | Site management: listing, create/update/delete, certificate status/renewal, validation. |
| `caddyfile.py` | Caddyfile viewer/editor page: view, save, validate-only, and trigger onboarding re-run. |
| `ssllabs.py` | SSL Labs scan UI: page render, start scan, update schedule, grade filtering (`_normalize_filter_grade`). |
| `about.py` | About page and update-check action. |
| `__init__.py` | Dynamically builds/exposes the combined UI `APIRouter` (`_build_router`, module `__getattr__`) so page routers can be composed without one giant import list. |

## For AI Agents

### Working In This Directory
- Every route that requires a logged-in user or admin should call `_common.require_user`/`require_admin` at the top — don't hand-roll session checks.
- State-changing POST/PATCH/DELETE handlers must validate CSRF via `_common.validated_csrf_form` (or `app/dependencies/web.validate_csrf_token` directly) before mutating anything.
- After a mutation, prefer `_common.commit_and_flash` so the flash-message UX stays consistent across pages.
- Any change to a user's password or OTP state must re-run `initialize_user_session(request, user)`; the session fingerprint covers both, so other sessions are invalidated.
- `sites.py` and `settings.py` are large and touch certificates/Caddy sync — read `app/services/certificates.py`, `caddy.py`, and `caddyfile_manager.py` alongside them before making non-trivial changes.

### Testing Requirements
- One `tests/test_ui_<module>.py` file per module here (e.g. `sites.py` ↔ `tests/test_ui_sites.py`, `dashboard.py` ↔ `tests/test_ui_dashboard.py`), plus `tests/test_ui_common.py` for `_common.py` and `tests/test_ui_toasts.py` for flash/toast rendering.

### Common Patterns
- Handlers return `Response`/render via `app.dependencies.web.render_template`, not raw dict responses.
- Local private helpers (prefixed `_`) do request-shape-specific formatting (e.g. `_serialize_certificate_info`, `_format_sse_data`-style helpers) close to the route that uses them.

## Dependencies

### Internal
- `app/services/*` (page data + mutations), `app/repositories/*` (occasionally, for simple lookups via services), `app/schemas/system.py`/`caddy.py` (where JSON is also returned to JS), `app/templates/*`.

### External
- `fastapi`, `sqlalchemy` (`AsyncSession` type hints for `Depends(get_db_session)`).

<!-- MANUAL: -->
