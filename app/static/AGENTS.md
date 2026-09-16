<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# static

## Purpose
Static assets served directly by the app: first-party CSS/JS/images plus vendored/bundled third-party JavaScript.

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `css/` | First-party stylesheets. |
| `js/` | First-party JavaScript (dashboard interactivity, forms, etc.). |
| `img/` | Images/icons/logos (e.g. `caddybuddy_1c.svg` used in README). |
| `vendor/` | Third-party/bundled JS, checked in rather than fetched from a CDN: `bootstrap/`, `chartjs/`, `codemirror/`, `confetti/`. |

## For AI Agents

### Working In This Directory
- `vendor/codemirror/` is a **build artifact**: it's produced by `tools/codemirror/` (`npm run build` there runs esbuild and writes `caddybuddy-codemirror.js` here). Do not hand-edit it — change the source in `tools/codemirror/` and rebuild instead.
- `bootstrap/`, `chartjs/`, and `confetti/` under `vendor/` are checked-in third-party libraries; treat them as read-only unless deliberately upgrading a vendored version.
- `app/dependencies/web.py` exposes `asset_integrity()` for SRI generation. When renaming, moving, or integrity-protecting assets, verify template references and cache invalidation together.

### Testing Requirements
- Use focused `../../tests/test_ui_*.py` checks for source-contract assertions and `../../tools/ui-lint/` for live browser, accessibility, and visual behavior.

## Dependencies

### Internal
- Referenced from `app/templates/*` via `asset_integrity()`/static URL helpers.

### External
- `tools/codemirror/` (build source for the CodeMirror bundle).

<!-- MANUAL: -->
