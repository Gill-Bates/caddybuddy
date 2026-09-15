<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# database

## Purpose
Async SQLAlchemy engine and session management for the SQLite-backed application database, including the file-locking bootstrap needed because the app runs against a single shared SQLite file.

## Key Files
| File | Description |
|------|-------------|
| `session.py` | Engine/session factory, `AsyncSession` creation, SQLite pragmas, and `fcntl`-based serialization of database initialization across processes. |
| `__init__.py` | Package marker. |

## For AI Agents

### Working In This Directory
- The `fcntl` sidecar lock in `session.py` serializes schema/database initialization across processes; ordinary requests rely on SQLite WAL, transactions, and connection pragmas rather than that lock. Do not broaden or weaken either mechanism without dedicated concurrency tests.
- Sessions should be obtained through the dependency-injection path (`app.dependencies.web` / FastAPI `Depends`), not instantiated ad hoc in routers.

### Testing Requirements
- See `../../tests/test_database_session.py` — this is a large, dedicated test file; extend it for any locking/session-lifecycle changes.

### Common Patterns
- Async context managers for session lifecycle; `threading`/`asyncio` primitives combined with `fcntl` for cross-process coordination.

## Dependencies

### Internal
- Used by `app/dependencies/web.py` and, transitively, every router/service that touches the database.

### External
- `sqlalchemy` (async engine), `aiosqlite`.

<!-- MANUAL: -->
