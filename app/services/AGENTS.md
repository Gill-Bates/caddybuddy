<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# services

## Purpose
Business logic layer, sitting between `app/routers/*` and `app/repositories/*`. This is where Caddy process control, the onboarding wizard, certificate inspection, SSL Labs scanning, and dashboard aggregation live. `caddy_onboarding.py` and `ssllabs.py` are the most frequently modified files in the whole codebase (per project history).

## Key Files
| File | Description |
|------|-------------|
| `caddy_onboarding.py` | First-run Caddy onboarding wizard service (largest file in `app/`, ~47KB). Drives the multi-step flow behind `app/routers/ui/onboarding.py`. Frequently modified — check `../../tests/test_caddy_onboarding.py` and `test_caddy_onboarding_flow.py` (also two of the largest test files) before changing behavior. |
| `caddy.py` | Core Caddy control/integration logic (status, Admin API interaction). |
| `caddyfile_manager.py` | Reading/writing/validating the managed Caddyfile, snapshotting, and versioning. |
| `ssllabs.py` | SSL Labs API integration: scan scheduling, polling, result ingestion, rank history. Frequently modified — see `../../tests/test_ssllabs_service.py` (largest test file in the repo). |
| `certificates.py` | Certificate inspection (expiry, issuer, chain status) used by the sites/dashboard UI. |
| `dashboard.py` | Aggregates metrics shown on the dashboard/home page and its API endpoint. |
| `renewal.py` | Certificate renewal orchestration. |
| `supervisor.py` | Process supervision helpers (e.g. managing/restarting the Caddy process). |
| `events.py` | In-memory event bus for broadcasting resource changes to connected clients via Server-Sent Events; see module docstring for subscribe/publish semantics. Single-process only. |
| `auth.py` | Authentication logic (credential verification, session bootstrap) backing `app/routers/ui/auth.py`. |
| `runtime_settings.py` | Runtime settings service for DB-stored configuration (the settings that can change without redeploying, e.g. rate-limiter enabled flag). |
| `about.py` | About-page data: runtime metadata, dependency versions, changelog, GitHub update checks. |
| `build_info.py` | Build/version metadata (git SHA, build date) surfaced via `/build-info`. |
| `__init__.py` | Package marker. |

## For AI Agents

### Working In This Directory
- `caddy_onboarding.py` and `ssllabs.py` are hot paths with the largest matching test files — treat any change here as needing thorough test coverage, not just a quick manual check.
- Services should depend on `app/repositories/*` for persistence and `app/utils/*` for stateless helpers (Caddyfile parsing, domain validation) rather than querying the DB or shelling out directly.
- `events.py`'s bus is in-process/in-memory — if the deployment model ever moves to multiple worker processes, SSE delivery would need rework; don't assume events fan out across processes today.

### Testing Requirements
- One `tests/test_<name>_service.py` (or similarly named) file per module — extend the matching file for any behavior change. `caddy_onboarding.py` in particular has two large, separate test files (`test_caddy_onboarding.py`, `test_caddy_onboarding_flow.py`).

### Common Patterns
- Async throughout; frequent use of `asyncio` primitives for coordinating with the external Caddy Admin API and SSL Labs API (`httpx`).
- File-locking (`fcntl`) reused in a couple of services (e.g. `renewal.py`) mirroring the pattern in `app/database/session.py`.

## Dependencies

### Internal
- `app/repositories/*`, `app/utils/*`, `app/models/entities.py` (return types), `app/config/settings.py`.

### External
- `httpx` (Caddy Admin API + SSL Labs API calls), `asyncio`, standard library.

<!-- MANUAL: -->
