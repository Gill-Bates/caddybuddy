<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# partials

## Purpose
Small reusable template fragments included by the page templates in `app/templates/`.

## Key Files
| File | Description |
|------|-------------|
| `flashes.html` | Renders flash messages pushed via `app.dependencies.web.push_flash`/`pop_flashes` (success/error/info banners). |
| `check_circle.svg` | Inline SVG icon (success/checkmark) reused across pages. |

## For AI Agents

### Working In This Directory
- Keep fragments here generic/parameterized (via Jinja2 `{% include %}` context) rather than page-specific — if a fragment starts needing page-specific branching, consider whether it belongs in the page template instead.

## Dependencies

### Internal
- Included by templates in the parent `app/templates/` directory.

<!-- MANUAL: -->
