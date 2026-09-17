<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# caddybuddy

## Purpose
CaddyBuddy is a focused, server-rendered web control plane for administering a single [Caddy](https://caddyserver.com/) installation: guided first-run onboarding, site and Caddyfile management, certificate visibility/renewal, scheduled SSL Labs assessments, and health monitoring. It is a Python 3.13 FastAPI application (async SQLAlchemy + SQLite, Jinja2 templates) shipped as a single Docker image.

## Key Files
| File | Description |
|------|-------------|
| `run.py` | Process entrypoint: builds uvicorn config/logging, prints startup banner, runs the ASGI app. |
| `pyproject.toml` | Single source of truth for project version and dependencies (build-system, `[project]`, `docs` extra, ruff config). Only the `app` package is distributed. |
| `Caddyfile` | Host/development starter configuration read by onboarding when available; it is not copied into the Docker image. |
| `setup.conf` | Maintainer command notes, not application configuration. Do not source it or treat embedded examples as credentials. |
| `mkdocs.yml` | Config for the MkDocs Material documentation site built from `docs/`. |
| `CHANGELOG.md` | Human-curated release history. |
| `README.md` | Project overview, screenshots, quick start. |
| `LICENSE` | MIT license text. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `app/` | The FastAPI application package — the only Python package actually shipped (see `pyproject.toml`). See `app/AGENTS.md`. |
| `docker/` | Container image (Dockerfile), entrypoint script, and Compose example. See `docker/AGENTS.md`. |
| `docs/` | MkDocs Material documentation source, published via GitHub Pages. See `docs/AGENTS.md`. |
| `tests/` | Pytest suite (plus a few standalone `.mjs` frontend-logic tests) mirroring `app/` module names. See `tests/AGENTS.md`. |
| `tools/` | Developer tooling: CodeMirror asset bundler and the Playwright-based UI lint/visual-regression harness. See `tools/AGENTS.md`. |

Not documented here (generated, runtime, or tool-local state, all gitignored): `site/` (MkDocs build output), `data/` (runtime SQLite DB + lock files), `.venv*`, `node_modules/`, `.omc/`, `.omx/`, `.claude/`, `.codex/`, `__pycache__/`.

## For AI Agents

### Working In This Directory
- This is a single-package project: only `app/` ships (see the `[tool.setuptools.packages.find]` comment in `pyproject.toml`). Everything else is dev/docs/CI tooling.
- Python 3.13 with async-first I/O (FastAPI + SQLAlchemy async ORM + `aiosqlite`). Keep I/O-bound paths async; pure helpers may remain synchronous.
- Templates are server-rendered Jinja2 (`app/templates/`) — this is not an SPA; there is no frontend build step for the main UI beyond the vendored/bundled JS in `app/static/vendor/`.
- The app uses one SQLite database with WAL/transaction coordination; an `fcntl` sidecar lock serializes cross-process initialization only. See `app/database/AGENTS.md` before touching session/engine code.

### Testing Requirements
- Use the repository-local environment for Python commands.
- Python: `.venv/bin/python -m pytest` from the repo root; tests in `tests/` generally follow the source module or behavior they cover.
- Lint: `.venv/bin/ruff check .`.
- Frontend/UI: `tools/ui-lint/` runs Playwright-based accessibility, visual-regression, and lint checks against the running app; narrow `.mjs` logic tests run with `node --test tests/*.mjs` from the repo root.

### Common Patterns
- Preferred layering: `routers/` orchestrate HTTP concerns, `services/` own non-trivial business workflows, `repositories/` own ordinary aggregate access, and `models/` define persistence. Existing routers may call repositories for simple lookups, and transaction-bound services may issue tightly scoped ORM operations; follow the local guide before adding a new exception. `schemas/` holds shared Pydantic API contracts.
- Copyright header convention: most `.py`/`.sh` files open with a `#!/usr/bin/env ...` + `Copyright (C) 2026 Gill-Bates` banner comment — match it in new files.

## Dependencies

### Internal
- `app/` is self-contained; `docker/` packages it; `docs/` and `tools/` are independent of `app/` at runtime (though `tools/codemirror/` builds an asset consumed by `app/static/vendor/codemirror/`).

### External
- Runtime: `fastapi`, `uvicorn[standard]`, `sqlalchemy`, `aiosqlite`, `pydantic`/`pydantic-settings`, `jinja2`, `slowapi` (rate limiting), `itsdangerous`, `bcrypt`, `cryptography`, `nh3` (HTML sanitization), `httpx`, `markdown`, `qrcode[pil]` (TOTP provisioning QR codes).
- Docs: `mkdocs` + `mkdocs-material` and plugins (`docs` optional-dependency group).

<!-- MANUAL: Any manually added notes below this line are preserved on regeneration -->
