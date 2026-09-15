<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# config

## Purpose
Application configuration: environment-driven settings, the shared rate limiter instance, and uvicorn/application logging setup.

## Key Files
| File | Description |
|------|-------------|
| `settings.py` | Main `pydantic-settings` model — env var parsing, validation (paths, timezone, URLs), and the cached `get_settings()` accessor. |
| `limiter.py` | Shared `slowapi.Limiter` instance (keyed by remote address). Created `enabled=True` by default; the enabled flag is updated at runtime from DB-stored settings (see `app/services/runtime_settings.py`). |
| `logging.py` | Customizes uvicorn's `LOGGING_CONFIG` (access/default formatters) for consistent structured log output. |
| `__init__.py` | Package marker. |

## For AI Agents

### Working In This Directory
- `get_settings()` is `@cache`d — settings are read once per process. Don't call the underlying constructor directly elsewhere; import and use `get_settings()`.
- The `Limiter` in `limiter.py` is a module-level singleton imported by routers/middleware that need rate limiting; its `enabled` state can be toggled at runtime — don't assume it's static.

### Testing Requirements
- See `../../tests/test_settings.py`, `test_logging_config.py`, `test_runtime_settings.py`.

### Common Patterns
- Settings use `pydantic.Field`/`AliasChoices` for env var aliasing and `field_validator`/`model_validator` for cross-field validation (e.g. URL normalization via `urlsplit`/`urlunsplit`).

## Dependencies

### Internal
- Consumed by nearly every other `app/` subpackage (routers, services, database, middleware) for configuration values.

### External
- `pydantic`, `pydantic-settings`, `slowapi`, `uvicorn`.

<!-- MANUAL: -->
