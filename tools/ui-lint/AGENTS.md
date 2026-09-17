<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# ui-lint

## Purpose
Playwright-based UI audit tool for CaddyBuddy: accessibility checks (axe-core), visual regression (pixelmatch-based screenshot diffing), and general lint/quality checks against the running app in real browsers (chromium/firefox/webkit, including mobile projects).

## Key Files
| File | Description |
|------|-------------|
| `run-ui-lint.mjs` | Main entry point (`npm run audit`); orchestrates the full audit run across pages/browsers and produces `ui-lint-summary.json`. |
| `package.json` | Audit/browser-install scripts and dependency declarations. Dependencies use unpinned `"latest"`, so refreshes can change behavior and must be reviewed with the lockfile diff. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `browser/` | Code injected into the page context during audits: `analyzers.bundle.js`, a hand-maintained (not built) bundle of all analyzers. |
| `lib/` | Node-side support library, including `node:test` unit tests in `*.test.mjs` and `totp.mjs` (TOTP code generation for logging into 2FA-enabled audit accounts). |
| `visual/` | Visual regression implementation and its `node:test` coverage; generated comparison output lives under the ignored `test-results/` tree. |
| `node_modules/` | npm dependencies (gitignored) — not documented. |
| `test-results/` | Playwright output (gitignored) — not documented. |

## For AI Agents

### Working In This Directory
- This tool drives a **running instance** of the app (start the app first, e.g. via `run.py` or Docker) — it is not a unit test suite for `app/` Python code.
- If the audit account has two-factor authentication enabled, `login()` (`lib/browser-utils.mjs`) automatically completes the `/login/otp` challenge using `lib/totp.mjs`; set the `UI_LINT_OTP_SECRET` env var or an `otpSecret` field in the credentials file to the account's TOTP secret, or login will fail with a two-factor-required error.
- `browser/` files run inside the page context (injected via `lib/inject-analyzers.mjs`) — they cannot use Node built-ins; keep them browser-safe.
- Dependencies are declared as `"latest"` in `package.json` — if an audit starts failing unexpectedly after installation, check the lockfile and upstream changes before assuming an app regression.

### Testing Requirements
- Run `npm test` (`node --test lib/*.test.mjs visual/*.test.mjs`) for the tracked Node unit tests.
- Run `npm run audit` against a running app for a full lint/accessibility pass; use `npm run audit:visual` to include Chromium visual regression.

### Common Patterns
- ESM (`.mjs`) throughout; browser-context code kept physically separate (`browser/`) from Node-context orchestration (`lib/`, root).

## Dependencies

### Internal
- Targets the running CaddyBuddy app at the base URL selected by the audit configuration, including the generated CodeMirror bundle exercised through the Caddyfile editor page.

### External
- `@playwright/test`, `@axe-core/playwright`, `chalk`, `fast-glob`, `lighthouse`, `mime-types`, `pixelmatch`, `pngjs`, `sharp`, `ssim.js`, `zod`.

<!-- MANUAL: -->
