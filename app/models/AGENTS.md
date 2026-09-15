<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# models

## Purpose
SQLAlchemy ORM entities for CaddyBuddy's SQLite database.

## Key Files
| File | Description |
|------|-------------|
| `base.py` | `DeclarativeBase` subclass and shared mixins (e.g. `TimestampMixin`), plus a custom `TypeDecorator`. |
| `entities.py` | All ORM entities: `User`, `CaddyBuddyState`, `CaddyfileSnapshot`, `CaddyConfigVersion`, `CaddySyncEvent`, `Site`, `SslLabsTarget`, `SslLabsScan`, `SslLabsRankHistory`, `AppSetting`. |
| `__init__.py` | Package marker (`"""ORM models for CaddyBuddy."""`). |

## For AI Agents

### Working In This Directory
- Models should only be queried/mutated from `app/repositories/*` — routers and services should not import `entities.py` to build queries directly (they may import entity types for type hints/return values).
- This project does not use Alembic-style migrations visible here; schema changes to `entities.py` should be checked against `app/database/session.py` init logic and `tests/test_entities_hardening.py`/`test_models_base.py` for expected constraints.
- `Site` and `SslLabsTarget`/`SslLabsScan`/`SslLabsRankHistory` model the core domain (a managed Caddy site and its SSL Labs assessment history); `CaddySyncEvent`/`CaddyConfigVersion`/`CaddyfileSnapshot` track Caddy config sync/versioning; `CaddyBuddyState` and `AppSetting` hold singleton/key-value app state.

### Testing Requirements
- See `../../tests/test_models_base.py` and `test_entities_hardening.py`.

### Common Patterns
- `TimestampMixin` for `created_at`/`updated_at` columns; string-based enums/constraints enforced via validators rather than DB-level `CHECK` constraints in places.

## Dependencies

### Internal
- Consumed exclusively by `app/repositories/*`.

### External
- `sqlalchemy` (`DeclarativeBase`, `Mapped`, `mapped_column`, `JSON`, `Boolean`, etc.).

<!-- MANUAL: -->
