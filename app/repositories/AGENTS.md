<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# repositories

## Purpose
Database access layer — one repository class per aggregate, wrapping ordinary `AsyncSession` queries against `app/models/entities.py`. A few transaction-bound services own tightly coupled snapshot/state operations; do not expand those exceptions casually.

## Key Files
| File | Description |
|------|-------------|
| `users.py` | `UserRepository` + `DuplicateUserError`; username/email normalization and password-hash/role validation helpers. |
| `sites.py` | `SiteRepository` + `DuplicateSiteError` for managed `Site` records. |
| `ssllabs.py` | `SslLabsRepository` plus helpers `active_scan_cutoff` and `site_uses_https`, for SSL Labs targets/scans/rank history. |
| `app_settings.py` | `AppSettingsRepository` for the key/value `AppSetting` table, including `_upsert_app_setting` (dialect-aware upsert) and key validation. |
| `__init__.py` | Package exports. |

## For AI Agents

### Working In This Directory
- Repositories should raise domain-specific exceptions (e.g. `DuplicateUserError`, `DuplicateSiteError`) rather than leaking raw `IntegrityError`s to callers — see `_is_duplicate_user_integrity_error` in `users.py` for the pattern of translating DB errors.
- Keep normalization/validation of DB-bound values (usernames, emails, setting keys, password hashes) here rather than duplicating it in services.

### Testing Requirements
- See `../../tests/test_user_repository.py`, `test_site_repository.py`, `test_app_settings_repository.py`. `ssllabs.py` is covered indirectly via `tests/test_ssllabs_service.py`.

### Common Patterns
- Async methods taking an `AsyncSession` parameter (session lifecycle owned by the caller, typically via `app/dependencies/web.py`).
- Dialect-aware upserts (see `app_settings.py`) since the app targets SQLite specifically.

## Dependencies

### Internal
- `app/models/entities.py` (the entities being queried); consumed by `app/services/*`.

### External
- `sqlalchemy` async ORM/`IntegrityError`.

<!-- MANUAL: -->
