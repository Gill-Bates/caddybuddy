<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# docker

## Purpose
Container image definition and runtime bootstrap for deploying CaddyBuddy.

## Key Files
| File | Description |
|------|-------------|
| `Dockerfile` | Multi-stage build producing the published `giiibates/caddybuddy` image for linux/amd64 and linux/arm64. The runtime stage intentionally excludes `pip` and build tooling. |
| `entrypoint.sh` | Container entrypoint. Runs as root to bootstrap `/app/data` and fix ownership/permissions, then drops privileges to the app user (UID/GID 1000) before starting the app. |
| `docker-compose.yml.example` | Reference Compose file (referenced directly in the README Quick Start). |
| `README_docker.md` | Docker-specific usage documentation. |

## For AI Agents

### Working In This Directory
- Check `../CHANGELOG.md` and recent commits before changing the image build or security posture.
- `entrypoint.sh` intentionally starts as root only to fix permissions on the mounted `data/` volume and Caddyfile, then execs as UID/GID 1000 — do not have the app itself run as root.
- Keep the final image free of build-only tooling (compilers, `pip`, etc.) — each build's `builder` stage resolves and installs dependencies fresh from `pyproject.toml` (unpinned) into a venv that is copied into the runtime stage, with `pip` itself removed before the copy.

### Testing Requirements
- See `../tests/test_dockerfile.py`, `../tests/test_docker_workflow.py`, and `../tests/test_entrypoint.py` — these assert on Dockerfile/entrypoint content and structure without requiring an actual Docker build.

## Dependencies

### Internal
- Packages `app/`, `run.py`, and build metadata; entrypoint provisions the `data/` volume consumed by `app/database/session.py`. The root `Caddyfile` is not copied into the image.

### External
- Debian-based Python base images and apt packages declared in `Dockerfile`; Python dependencies are resolved from `pyproject.toml` at image build time (`--no-cache` in the release workflow ensures a fresh resolution per release).

<!-- MANUAL: -->
