<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# docker

## Purpose
Container image definition and runtime bootstrap for deploying CaddyBuddy. Frequently modified (one of the hottest paths in the repo, per project history) — recent changes applied Debian security updates and dropped `pip` from the final image.

## Key Files
| File | Description |
|------|-------------|
| `Dockerfile` | Multi-stage build producing the published `giiibates/caddybuddy` image (see README badges) for linux/amd64 and linux/arm64. Security-hardened: recent work removed `pip` from the runtime image and pinned Debian security updates — preserve that hardening in future changes rather than reintroducing build tooling into the final stage. |
| `entrypoint.sh` | Container entrypoint. Runs as root to bootstrap `/app/data` and fix ownership/permissions, then drops privileges to the app user (UID/GID 1000) before starting the app. |
| `docker-compose.yml.example` | Reference Compose file (referenced directly in the README Quick Start). |
| `README_docker.md` | Docker-specific usage documentation. |

## For AI Agents

### Working In This Directory
- This directory is a hot path — check `../../CHANGELOG.md` and recent commits before assuming current behavior; the image build/security posture changes often.
- `entrypoint.sh` intentionally starts as root only to fix permissions on the mounted `data/` volume and Caddyfile, then execs as UID/GID 1000 — do not have the app itself run as root.
- Keep the final image free of build-only tooling (compilers, `pip`, etc.) — dependencies are resolved once by CI (see `.github/workflows/docker-build.yml`, referenced from `pyproject.toml`) and installed from the resolved set, not compiled in the final stage.

### Testing Requirements
- See `../../tests/test_dockerfile.py`, `test_docker_workflow.py`, `test_entrypoint.py` — these assert on Dockerfile/entrypoint content and structure without requiring an actual Docker build.

## Dependencies

### Internal
- Packages `app/` and the root `Caddyfile`; entrypoint provisions the `data/` volume consumed by `app/database/session.py`.

### External
- Base image and OS packages pinned in `Dockerfile` (Debian-based); Python deps resolved from `pyproject.toml`.

<!-- MANUAL: -->
