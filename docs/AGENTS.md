<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# docs

## Purpose
Source content for the MkDocs Material documentation site, published to GitHub Pages (`https://gill-bates.github.io/caddybuddy/`). Built via `mkdocs.yml` at the repo root into `../site/` (generated, gitignored — never edit `site/` by hand).

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `getting-started/` | Installation, quick start, and first-run Markdown guides. |
| `configuration/` | Caddy runtime and environment configuration reference. |
| `features/` | Per-feature documentation for dashboard, sites, Caddyfile, SSL Labs, and About. |
| `operations/` | Health-check and security operations documentation. |
| `development/` | Architecture and local development documentation. |
| `assets/` | Images and static assets embedded in docs pages. |
| `stylesheets/` | Custom CSS overrides for the MkDocs Material theme (`extra.css`, referenced in `mkdocs.yml`). |

## For AI Agents

### Working In This Directory
- This is documentation content (Markdown), not application code — changes here don't require `pytest`, but should stay consistent with actual `app/` behavior (this repo has `tests/test_docs_workflow.py` asserting on docs build/workflow expectations).
- `docs/changelog.md` and `docs/license.md` are copied from `../CHANGELOG.md` and `../LICENSE` by `.github/workflows/docs-build.yml` and are gitignored. Edit the root sources, never these generated copies.
- Navigation structure and plugin config (git-revision-date, minify, redirects) live in `../mkdocs.yml`, not here — update both together when adding/moving pages.

### Testing Requirements
- `tests/test_docs_workflow.py` covers docs build/workflow assumptions; a real `mkdocs build` (extras in `pyproject.toml`'s `docs` group) is the practical way to verify rendering.

## Dependencies

### External
- `mkdocs`, `mkdocs-material`, `mkdocs-git-revision-date-localized-plugin`, `mkdocs-minify-plugin`, `mkdocs-redirects`, `pymdown-extensions` (all pinned in the `docs` optional-dependency group of `../pyproject.toml`).

<!-- MANUAL: -->
