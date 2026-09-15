<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# caddybuddy

## Purpose
CaddyBuddy is a focused, server-rendered web control plane for administering a single [Caddy](https://caddyserver.com/) installation: guided first-run onboarding, site and Caddyfile management, certificate visibility/renewal, scheduled SSL Labs assessments, and health monitoring. It is a Python 3.13 FastAPI application (async SQLAlchemy + SQLite, Jinja2 templates) shipped as a single Docker image.

## Key Files
| File | Description |
|------|-------------|
| `run.py` | Process entrypoint: builds uvicorn config/logging, prints startup banner, runs the ASGI app. |
| `pyproject.toml` | Single source of truth for project version and dependencies (build-system, `[project]`, `docs` extra, ruff config). Only the `app` package is distributed. |
| `Caddyfile` | Default/template Caddy configuration used by the app and Docker image. |
| `setup.conf` | Local/onboarding setup configuration (non-Docker deployments). |
| `mkdocs.yml` | Config for the MkDocs Material documentation site built from `docs/`. |
| `CHANGELOG.md` | Human-curated release history. |
| `README.md` | Project overview, screenshots, quick start. |
| `LICENSE` | AGPL-3.0 license text. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `app/` | The FastAPI application package — the only Python package actually shipped (see `pyproject.toml`). See `app/AGENTS.md`. |
| `docker/` | Container image (Dockerfile), entrypoint script, and Compose example. See `docker/AGENTS.md`. |
| `docs/` | MkDocs Material documentation source, published via GitHub Pages. See `docs/AGENTS.md`. |
| `tests/` | Pytest suite (plus a couple of standalone `.mjs` frontend-logic tests) mirroring `app/` module names. See `tests/AGENTS.md`. |
| `tools/` | Developer tooling: CodeMirror asset bundler and the Playwright-based UI lint/visual-regression harness. See `tools/AGENTS.md`. |

Not documented here (generated, runtime, or tool-local state, all gitignored): `site/` (MkDocs build output), `data/` (runtime SQLite DB + lock files), `.venv*`, `node_modules/`, `.omc/`, `.omx/`, `.claude/`, `.codex/`, `__pycache__/`.

## For AI Agents

### Working In This Directory
- This is a single-package project: only `app/` ships (see the `[tool.setuptools.packages.find]` comment in `pyproject.toml`). Everything else is dev/docs/CI tooling.
- Python 3.13, fully async (FastAPI + SQLAlchemy async ORM + `aiosqlite`). Keep new I/O-bound code async.
- Templates are server-rendered Jinja2 (`app/templates/`) — this is not an SPA; there is no frontend build step for the main UI beyond the vendored/bundled JS in `app/static/vendor/`.
- The app is designed for a single SQLite database file with file-locking coordination (`fcntl`) for multi-worker safety — see `app/database/AGENTS.md` before touching session/engine code.

### Testing Requirements
- Python: `pytest` from the repo root; test files in `tests/` are named `test_<module>.py` mirroring the `app/` module they cover.
- Lint: `ruff check`.
- Frontend/UI: `tools/ui-lint/` runs Playwright-based accessibility, visual-regression, and lint checks against the running app; a couple of narrow `.mjs` logic tests live directly in `tests/`.

### Common Patterns
- Layering: `routers/` (HTTP layer, both JSON API and server-rendered UI) → `services/` (business logic) → `repositories/` (DB access) → `models/` (SQLAlchemy ORM). `schemas/` holds Pydantic request/response models for the JSON API.
- Copyright header convention: most `.py`/`.sh` files open with a `#!/usr/bin/env ...` + `Copyright (C) 2026 Gill-Bates` banner comment — match it in new files.

## Dependencies

### Internal
- `app/` is self-contained; `docker/` packages it; `docs/` and `tools/` are independent of `app/` at runtime (though `tools/codemirror/` builds an asset consumed by `app/static/vendor/codemirror/`).

### External
- Runtime: `fastapi`, `uvicorn[standard]`, `sqlalchemy`, `aiosqlite`, `pydantic`/`pydantic-settings`, `jinja2`, `slowapi` (rate limiting), `itsdangerous`, `bcrypt`, `cryptography`, `nh3` (HTML sanitization), `httpx`, `markdown`.
- Docs: `mkdocs` + `mkdocs-material` and plugins (`docs` optional-dependency group).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
