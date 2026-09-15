<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# templates

## Purpose
Jinja2 templates rendered by `app/routers/ui/*` — the entire visible application UI.

## Key Files
| File | Description |
|------|-------------|
| `base.html` | Shared page layout (nav, CSP nonce wiring, flash-message rendering, asset includes) extended by every other page template. |
| `login.html` | Sign-in page (`app/routers/ui/auth.py`), including the invisible-captcha markup. |
| `home.html` | Dashboard/home page (`app/routers/ui/dashboard.py`). |
| `onboarding.html` | First-run onboarding wizard (`app/routers/ui/onboarding.py`) — the largest template. |
| `sites.html` | Site management page (`app/routers/ui/sites.py`) — second largest template. |
| `settings.html` | App settings page (`app/routers/ui/settings.py`). |
| `caddyfile.html` | Caddyfile viewer/editor (`app/routers/ui/caddyfile.py`), embeds the CodeMirror editor bundle. |
| `ssllabs.html` | SSL Labs scan results/schedule page (`app/routers/ui/ssllabs.py`). |
| `about.html` | About page (`app/routers/ui/about.py`). |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `partials/` | Small reusable template fragments included by the pages above. See `partials/AGENTS.md`. |

## For AI Agents

### Working In This Directory
- Full-page templates extend `base.html`; fragments under `partials/` are included and do not extend a layout. Keep shared chrome (nav, flash rendering, CSP nonce attributes) in the base template rather than duplicating it per page.
- Any inline `<script>` must carry the CSP nonce (see `ensure_csp_nonce` usage in `app/dependencies/web.py`) or it will be blocked by the Content-Security-Policy.
- Template filenames map 1:1 to a router module in `app/routers/ui/` of (mostly) the same name — check that router for the exact context variables a template can expect.

### Testing Requirements
- Rendering correctness is covered by `tests/test_ui_*.py` (asserting on rendered HTML) and by `tools/ui-lint/` (Playwright accessibility/visual checks against the live pages).

### Common Patterns
- Server-rendered forms posting back to the same route (POST-redirect-GET pattern) with CSRF hidden fields and flash-message feedback.

## Dependencies

### Internal
- Rendered via `app/dependencies/web.render_template`; data supplied by `app/routers/ui/*` and, transitively, `app/services/*`.

### External
- `jinja2`.

<!-- MANUAL: -->
