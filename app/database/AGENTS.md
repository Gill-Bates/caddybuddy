<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# database

## Purpose
Async SQLAlchemy engine and session management for the SQLite-backed application database, including the file-locking bootstrap needed because the app runs against a single shared SQLite file.

## Key Files
| File | Description |
|------|-------------|
| `session.py` | Engine/session factory, `AsyncSession` creation, and `fcntl`-based file locking (coordinating DB init/access across processes/workers sharing `data/caddybuddy.db`). The largest file in this package. |
| `__init__.py` | Package marker. |

## For AI Agents

### Working In This Directory
- The `fcntl` file locking in `session.py` (paired with lock files under `data/` and `data/locks/`) exists specifically to make single-file SQLite safe across multiple worker processes/threads. Do not remove or weaken this locking without understanding the multi-worker deployment story (see `docker/entrypoint.sh` for how `data/` is bootstrapped).
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
