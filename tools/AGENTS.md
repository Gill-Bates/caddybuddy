<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# tools

## Purpose
Developer tooling that is not part of the shipped Python package: an optional-dependency export helper for CI and documentation builds, a JS asset bundler for the CodeMirror-based Caddyfile editor, and a Playwright-based UI lint/visual-regression/accessibility harness.

## Key Files
| File | Description |
|------|-------------|
| `pyproject-deps.py` | Standalone script used by CI and documentation commands to print one optional dependency group from `../pyproject.toml` as a flat requirements list. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `codemirror/` | Bundles the CodeMirror 6 editor (used in the Caddyfile editor page) into `app/static/vendor/codemirror/` via esbuild. See `codemirror/AGENTS.md`. |
| `ui-lint/` | Playwright-based UI audit tool: accessibility (axe-core), visual regression (pixelmatch), and general lint checks against the running app. See `ui-lint/AGENTS.md`. |

## For AI Agents

### Working In This Directory
- Nothing here ships as Python runtime code — it is build-time/dev-time tooling. CodeMirror's bundler intentionally imports first-party modules from `app/static/js/`; production `app` code must not import from `tools/`.
- Both `codemirror/` and `ui-lint/` are independent Node projects with their own `package.json`/`node_modules` (gitignored) — install dependencies per-directory, not from the repo root.

### Testing Requirements
- For `codemirror/`, run `npm run build` and the focused Python UI tests documented in its local guide. For `ui-lint/`, run the tracked `node:test` files directly and use `npm run audit` against a running app; see its local guide for exact commands.

## Dependencies

### External
- Node.js/npm (both subdirectories); `esbuild` (`codemirror/`); `@playwright/test`, `@axe-core/playwright`, `pixelmatch`, `lighthouse` (`ui-lint/`).

<!-- MANUAL: -->
