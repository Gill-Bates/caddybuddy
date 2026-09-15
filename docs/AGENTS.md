<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# docs

## Purpose
Source content for the MkDocs Material documentation site, published to GitHub Pages (`https://gill-bates.github.io/caddybuddy/`). Built via `mkdocs.yml` at the repo root into `../site/` (generated, gitignored — never edit `site/` by hand).

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `getting-started/` | Installation, quick start, first-run guides (`installation/`, `quick-start/`, `first-run/`). |
| `configuration/` | Configuration reference (`caddy-runtime/`, `environment/`). |
| `features/` | Per-feature docs: `dashboard/`, `sites/`, `caddyfile/`, `ssl-labs/`. |
| `operations/` | Operational docs: `health-checks/`, `security/`. |
| `development/` | Contributor/dev docs, including `architecture/` and `setup/`. |
| `assets/` | Images and static assets embedded in docs pages. |
| `stylesheets/` | Custom CSS overrides for the MkDocs Material theme (`extra.css`, referenced in `mkdocs.yml`). |

## For AI Agents

### Working In This Directory
- This is documentation content (Markdown), not application code — changes here don't require `pytest`, but should stay consistent with actual `app/` behavior (this repo has `tests/test_docs_workflow.py` asserting on docs build/workflow expectations).
- `docs/changelog.md` and `docs/license.md` are generated/synced and gitignored (see root `.gitignore`) — don't hand-author them; they likely mirror `../CHANGELOG.md`/`../LICENSE`.
- Navigation structure and plugin config (git-revision-date, minify, redirects) live in `../mkdocs.yml`, not here — update both together when adding/moving pages.

### Testing Requirements
- `tests/test_docs_workflow.py` covers docs build/workflow assumptions; a real `mkdocs build` (extras in `pyproject.toml`'s `docs` group) is the practical way to verify rendering.

## Dependencies

### External
- `mkdocs`, `mkdocs-material`, `mkdocs-git-revision-date-localized-plugin`, `mkdocs-minify-plugin`, `mkdocs-redirects`, `pymdown-extensions` (all pinned in the `docs` optional-dependency group of `../pyproject.toml`).

<!-- MANUAL: -->
