<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# tools

## Purpose
Developer tooling that is not part of the shipped Python package: a dependency-resolution helper for the Docker release workflow, a JS asset bundler for the CodeMirror-based Caddyfile editor, and a Playwright-based UI lint/visual-regression/accessibility harness.

## Key Files
| File | Description |
|------|-------------|
| `pyproject-deps.py` | Standalone script referenced from `../pyproject.toml`'s comments and `.github/workflows/docker-build.yml`: resolves the newest resolvable dependency set once for both linux/amd64 and linux/arm64 so both architecture builds get identical pinned versions. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `codemirror/` | Bundles the CodeMirror 6 editor (used in the Caddyfile editor page) into `app/static/vendor/codemirror/` via esbuild. See `codemirror/AGENTS.md`. |
| `ui-lint/` | Playwright-based UI audit tool: accessibility (axe-core), visual regression (pixelmatch), and general lint checks against the running app. See `ui-lint/AGENTS.md`. |

## For AI Agents

### Working In This Directory
- Nothing here ships in the Docker image or the Python package — it's build-time/dev-time only. Don't add runtime imports from `app/` into anything here (or vice versa, beyond the built `codemirror` JS artifact).
- Both `codemirror/` and `ui-lint/` are independent Node projects with their own `package.json`/`node_modules` (gitignored) — install dependencies per-directory, not from the repo root.

### Testing Requirements
- `codemirror/` has its own small test file (`scan-braces.test.mjs`); `ui-lint/` runs via Playwright (`npm test` / `npm run audit` inside that directory).

## Dependencies

### External
- Node.js/npm (both subdirectories); `esbuild` (`codemirror/`); `@playwright/test`, `@axe-core/playwright`, `pixelmatch`, `lighthouse` (`ui-lint/`).

<!-- MANUAL: -->
