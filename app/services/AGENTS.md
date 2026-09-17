<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# services

## Purpose
Business logic layer, sitting between `app/routers/*` and persistence/integration boundaries. This is where Caddy process control, the onboarding wizard, certificate inspection, SSL Labs scanning, and dashboard aggregation live.

## Key Files
| File | Description |
|------|-------------|
| `caddy_onboarding.py` | First-run Caddy onboarding wizard service. Drives the multi-step flow behind `app/routers/ui/onboarding.py`; check `../../tests/test_caddy_onboarding.py` and `test_caddy_onboarding_flow.py` before changing behavior. |
| `caddy.py` | Core Caddy control/integration logic (status, Admin API interaction). |
| `caddyfile_manager.py` | Reading/writing/validating the managed Caddyfile, snapshotting, and versioning. |
| `ssllabs.py` | SSL Labs API integration: scan scheduling, polling, result ingestion, rank history. See `../../tests/test_ssllabs_service.py`. |
| `certificates.py` | Certificate inspection (expiry, issuer, chain status) used by the sites/dashboard UI. |
| `dashboard.py` | Aggregates metrics shown on the dashboard/home page and its API endpoint. |
| `renewal.py` | Certificate renewal orchestration. |
| `supervisor.py` | Process supervision helpers (e.g. managing/restarting the Caddy process). |
| `events.py` | In-memory event bus for broadcasting resource changes to connected clients via Server-Sent Events; see module docstring for subscribe/publish semantics. Single-process only. |
| `auth.py` | Authentication logic (credential verification, session bootstrap) backing `app/routers/ui/auth.py`, plus TOTP enrollment/verification: Fernet encryption of OTP secrets and recovery-code keys are derived from the password pepper (falling back to `CB_SECRET_KEY` when no pepper is configured), replay protection via repository counter updates. |
| `runtime_settings.py` | Runtime settings service for DB-stored configuration (the settings that can change without redeploying, e.g. rate-limiter enabled flag), including the nh3-sanitized maintenance page HTML. |
| `about.py` | About-page data: runtime metadata, dependency versions, changelog, GitHub update checks. |
| `build_info.py` | Build/version metadata (git SHA, build date) surfaced via `/build-info`. |
| `__init__.py` | Package marker. |

## For AI Agents

### Working In This Directory
- Treat changes to `caddy_onboarding.py` and `ssllabs.py` as requiring focused regression coverage.
- Prefer repositories for ordinary aggregate persistence and utilities for stateless helpers. Existing transaction-bound services may own tightly coupled snapshot/state queries; process execution belongs in the validated `supervisor.py` adapters rather than ad hoc subprocess calls elsewhere.
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
