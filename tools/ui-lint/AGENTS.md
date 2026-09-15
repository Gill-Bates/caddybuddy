<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# ui-lint

## Purpose
Playwright-based UI audit tool for CaddyBuddy: accessibility checks (axe-core), visual regression (pixelmatch-based screenshot diffing), and general lint/quality checks against the running app in real browsers (chromium/firefox/webkit, including mobile projects).

## Key Files
| File | Description |
|------|-------------|
| `run-ui-lint.mjs` | Main entry point (`npm run audit`) — the largest file in this tool (~65KB); orchestrates the full audit run across pages/browsers and produces `ui-lint-summary.json`. |
| `playwright.config.mjs` | Playwright project/browser configuration (chromium/firefox/webkit + mobile projects referenced in `package.json` scripts). |
| `test-click.mjs` | Small standalone click/interaction script. |
| `visual-regression.mjs` | Thin entry point delegating to `visual/visual-regression.mjs`. |
| `package.json` | Scripts: `audit`, `audit:visual`, `install:browsers`(`:ci`), `test`/`ci:test`/`test:mobile`(`:update-snapshots`)/`test:headed`/`test:ui`, `report`. Dependencies use unpinned `"latest"` — expect version drift; re-run `npm install` if behavior seems stale. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `browser/` | Code injected into the page context during audits: `analyzers/accessibility.js` (axe-core-based accessibility analyzer), `analyzers.bundle.js` (bundled analyzers for injection), `utils/dom-cache.js` (DOM query caching for analyzer performance). |
| `lib/` | Node-side support library: `constants.mjs`, `browser-utils.mjs` (+ its `.test.mjs`), `device-page-pool.mjs` (browser/page pooling across device projects), `findings.mjs` (+ `.test.mjs`, finding/result data model), `inject-analyzers.mjs` (injects `browser/` code into pages), `result-serializer.mjs`, `views.mjs` (report/output formatting). |
| `visual/` | Visual regression implementation: `visual-regression.mjs` (+ `.test.mjs`) and baseline `artifacts/` (gitignored). |
| `node_modules/` | npm dependencies (gitignored) — not documented. |
| `test-results/` | Playwright output (gitignored) — not documented. |

## For AI Agents

### Working In This Directory
- This tool drives a **running instance** of the app (start the app first, e.g. via `run.py` or Docker) — it is not a unit test suite for `app/` Python code.
- `browser/` files run inside the page context (injected via `lib/inject-analyzers.mjs`) — they cannot use Node built-ins; keep them browser-safe.
- Dependencies are pinned to `"latest"` in `package.json` — if an audit starts failing unexpectedly, check for an upstream (Playwright/axe-core/lighthouse) version bump before assuming an app regression.

### Testing Requirements
- `npm test` / `npm run ci:test` (Playwright) for the tool's own test suite (`lib/*.test.mjs`, `visual/visual-regression.test.mjs`).
- `npm run audit` for a full lint/accessibility pass; `npm run audit:visual` to include visual regression (chromium only, single-worker by default).
- `npm run test:mobile` for mobile-specific projects.

### Common Patterns
- ESM (`.mjs`) throughout; browser-context code kept physically separate (`browser/`) from Node-context orchestration (`lib/`, root).

## Dependencies

### Internal
- Targets the running CaddyBuddy app (any base URL configured in `playwright.config.mjs`); its output (`app/static/vendor/codemirror/`) is exercised indirectly via the Caddyfile editor page.

### External
- `@playwright/test`, `@axe-core/playwright`, `chalk`, `fast-glob`, `lighthouse`, `mime-types`, `pixelmatch`.

<!-- MANUAL: -->
