<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# ui-lint

## Purpose
Playwright-based UI audit tool for CaddyBuddy: accessibility checks (axe-core), visual regression (pixelmatch-based screenshot diffing), and general lint/quality checks against the running app in real browsers (chromium/firefox/webkit, including mobile projects).

## Key Files
| File | Description |
|------|-------------|
| `run-ui-lint.mjs` | Main entry point (`npm run audit`); orchestrates the full audit run across pages/browsers and produces `ui-lint-summary.json`. |
| `playwright.config.mjs` | Playwright project/browser configuration (chromium/firefox/webkit + mobile projects referenced in `package.json` scripts). |
| `test-click.mjs` | Small standalone click/interaction script. |
| `visual-regression.mjs` | Thin entry point delegating to `visual/visual-regression.mjs`. |
| `package.json` | Audit/browser-install scripts and dependency declarations. Dependencies use unpinned `"latest"`, so refreshes can change behavior and must be reviewed with the lockfile diff. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `browser/` | Code injected into the page context during audits: `analyzers/accessibility.js` (axe-core-based accessibility analyzer), `analyzers.bundle.js` (bundled analyzers for injection), `utils/dom-cache.js` (DOM query caching for analyzer performance). |
| `lib/` | Node-side support library, including `node:test` unit tests in `*.test.mjs`. |
| `visual/` | Visual regression implementation and its `node:test` coverage; generated comparison output lives under the ignored `test-results/` tree. |
| `node_modules/` | npm dependencies (gitignored) — not documented. |
| `test-results/` | Playwright output (gitignored) — not documented. |

## For AI Agents

### Working In This Directory
- This tool drives a **running instance** of the app (start the app first, e.g. via `run.py` or Docker) — it is not a unit test suite for `app/` Python code.
- `browser/` files run inside the page context (injected via `lib/inject-analyzers.mjs`) — they cannot use Node built-ins; keep them browser-safe.
- Dependencies are declared as `"latest"` in `package.json` — if an audit starts failing unexpectedly after installation, check the lockfile and upstream changes before assuming an app regression.

### Testing Requirements
- Run `node --test lib/*.test.mjs visual/*.test.mjs` for the tracked Node unit tests.
- Run `npm run audit` against a running app for a full lint/accessibility pass; use `npm run audit:visual` to include Chromium visual regression.
- The Playwright `npm test` and mobile scripts expect `tests/**/*.spec.*`, which are not currently tracked; do not use them as the default verification command until that suite exists in the repository.

### Common Patterns
- ESM (`.mjs`) throughout; browser-context code kept physically separate (`browser/`) from Node-context orchestration (`lib/`, root).

## Dependencies

### Internal
- Targets the running CaddyBuddy app at the base URL selected by the audit configuration, including the generated CodeMirror bundle exercised through the Caddyfile editor page.

### External
- `@playwright/test`, `@axe-core/playwright`, `chalk`, `fast-glob`, `lighthouse`, `mime-types`, `pixelmatch`, `pngjs`, `sharp`, `ssim.js`, `zod`.

<!-- MANUAL: -->
