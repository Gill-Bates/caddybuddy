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
- Assets referenced by UI pages get Subresource Integrity hashes computed via `app/dependencies/web.py`'s `asset_integrity()` — renaming/moving files here may require checking template references still resolve.

### Testing Requirements
- Visual/behavioral coverage for static assets comes from `tools/ui-lint/` (Playwright), not `pytest`.

## Dependencies

### Internal
- Referenced from `app/templates/*` via `asset_integrity()`/static URL helpers.

### External
- `tools/codemirror/` (build source for the CodeMirror bundle).

<!-- MANUAL: -->
