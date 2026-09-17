<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# codemirror

## Purpose
Builds the CodeMirror 6 editor used by the Caddyfile editor page (`app/routers/ui/caddyfile.py` / `app/templates/caddyfile.html`) into a single bundled JS file consumed by the app.

## Key Files
| File | Description |
|------|-------------|
| `caddybuddy-codemirror.mjs` | Esbuild entry point that re-exports `initialize` from `app/static/js/caddy-editor.js`; that module imports CodeMirror packages and the first-party brace scanner into the browser bundle. |
| `package.json` | Defines the `build` script: `esbuild` bundles `caddybuddy-codemirror.mjs` into `../../app/static/vendor/codemirror/caddybuddy-codemirror.js` (IIFE format, global name `CaddyBuddyCodeMirror`, minified). |

## For AI Agents

### Working In This Directory
- The build **output** lives in `app/static/vendor/codemirror/` — that file is generated, not hand-edited. After changing anything here, run `npm run build` (from this directory) and commit the regenerated bundle alongside the source change.
- This package is pinned (exact versions in `package.json`, unlike `ui-lint/` which uses `"latest"`) — bump versions deliberately, not incidentally.

### Testing Requirements
- Run `npm run build`, then run `.venv/bin/python -m pytest tests/test_ui_caddyfile.py` from the repository root. This package has no test script of its own; the bundled brace scanner (`app/static/js/caddy-editor-braces.js`) is covered by `tests/test_caddy_editor_braces.mjs` (`node --test tests/test_caddy_editor_braces.mjs`).

## Dependencies

### Internal
- Output consumed by `app/templates/caddyfile.html` via `app/static/vendor/codemirror/caddybuddy-codemirror.js`.

### External
- `@codemirror/*` packages (pinned versions), `codemirror` meta-package, `esbuild`.

<!-- MANUAL: -->
