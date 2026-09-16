<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# middleware

## Purpose
ASGI middleware for the server-rendered UI: CSRF protection and session-cookie handling.

## Key Files
| File | Description |
|------|-------------|
| `csrf.py` | CSRF protection middleware for UI routes — validates state-changing requests against the token issued via `app.dependencies.web.ensure_csrf_token`. |
| `session.py` | Session middleware with request-aware `Secure` cookie handling (e.g. only marking cookies `Secure` when the request is actually over HTTPS/behind a trusted proxy). |
| `__init__.py` | Package marker (`"""Application middleware."""`). |

## For AI Agents

### Working In This Directory
- These two middlewares are security-critical and registered in `app/main.py`'s app factory. Order of middleware registration matters (session must be available before CSRF validation runs) — check `main.py` before reordering.
- `csrf.py` works together with `app/dependencies/web.py` (`ensure_csrf_token`/`validate_csrf_token`) and `app/routers/ui/_common.py` (`validated_csrf_form`) — treat these three as one feature when changing CSRF behavior.

### Testing Requirements
- See `../../tests/test_security_middleware.py`.

### Common Patterns
- Session cookies contain Base64-encoded JSON and are integrity-protected and timestamped with `itsdangerous.TimestampSigner`; Base64 is encoding, not the security boundary. Preserve signature and expiry validation when changing the format.

## Dependencies

### Internal
- `app/config/settings.py` (cookie/secret config), `app/dependencies/web.py` (CSRF token helpers).

### External
- `fastapi`/Starlette middleware base classes.

<!-- MANUAL: -->
