<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# dependencies

## Purpose
FastAPI dependency-injection helpers (`Depends(...)`) shared across the server-rendered UI routes: session/auth resolution, CSRF token issuance/validation, CSP nonces, flash messages, cache-busted asset integrity hashes, and safe-redirect helpers.

## Key Files
| File | Description |
|------|-------------|
| `web.py` | All of the above. Key functions include `get_session_user` (resolve the logged-in `User` from the session cookie), `require_api_user`/`require_admin_api_user` (the shared `Depends(...)` auth gates for JSON API routes — 401 anonymous, 403 non-admin), `ensure_csrf_token`/`validate_csrf_token`, `ensure_csp_nonce`, `push_flash`/`pop_flashes`, `asset_integrity`/`_asset_integrity_cached` (SRI hashes for static assets, mtime/size-cached), `safe_redirect_path`/`redirect_to`, `initialize_user_session`, and `render_template`. |
| `__init__.py` | Package marker (`"""Dependency helpers for web routes."""`). |

## For AI Agents

### Working In This Directory
- These helpers are consumed heavily by `app/routers/ui/*` and `app/routers/ui/_common.py`. Changes to session/CSRF behavior here affect every UI page.
- `asset_integrity()` computes/caches SRI hashes keyed by file mtime+size — if you change how static assets are built or served, verify cache invalidation still works.
- `_csrf_secret()` is `@cache`d — it derives from settings once per process; don't reintroduce per-request secret derivation.

### Testing Requirements
- Covered indirectly by the many `tests/test_ui_*.py` files and directly by `../../tests/test_security_middleware.py` for CSRF/session behavior.

### Common Patterns
- HMAC-based CSRF token validation (`hmac`, `sha256`/`sha384`) rather than a third-party CSRF library.

## Dependencies

### Internal
- Relies on `app/config/settings.py` (secrets, cookie config), `app/database/session.py` (session param), `app/models/entities.py` (`User`).

### External
- `fastapi` (`Request`, `Response`), standard library `hmac`/`hashlib`/`secrets`.

<!-- MANUAL: -->
