<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-09-15 | Updated: 2026-09-15 -->

# utils

## Purpose
Stateless helper modules with no dependency on the database or FastAPI request context — pure(ish) functions consumed by `services/`, `repositories/`, and `routers/`.

## Key Files
| File | Description |
|------|-------------|
| `caddyfile.py` | Caddyfile parsing/generation logic behind `services/caddyfile_manager.py`. |
| `admin_targets.py` | Shared allow/deny policy for Caddy Admin API network targets. Used by **both** the runtime-settings validator (checks a user-supplied Admin API URL before persisting) and the Admin API client (pins a resolved IP before connecting) — security-relevant (SSRF prevention); keep both call sites in sync. |
| `hidden_captcha.py` | Invisible, no-interaction anti-bot validation for public auth forms (honeypot-style, no user-facing CAPTCHA challenge). |
| `security_logging.py` | Stable, injection-safe security event log formatting for external log consumers — don't interpolate untrusted input into these messages without going through the helpers here. |
| `otp.py` | RFC 6238 TOTP verification (current time step only, stdlib-only), provisioning URI/QR data URL (uses the third-party `qrcode` package), and keyed single-use recovery-code hashing. Security-relevant; encryption of stored secrets lives in `services/auth.py`. |
| `domains.py` | Domain name validation/normalization helpers, including IP-address detection (`ipaddress`). |
| `parsing.py` | Small generic parsing helpers (dates, JSON) shared across services. |
| `ssllabs.py` | SSL Labs-specific formatting/calculation helpers (grades, hashing) supporting `services/ssllabs.py`. |
| `banner.py` | Startup banner printed once at process start (`run.py`). |
| `version.py` | Application version resolution. |
| `__init__.py` | Package marker. |

## For AI Agents

### Working In This Directory
- `admin_targets.py` is a security boundary (SSRF prevention for the Caddy Admin API) — changes here need extra scrutiny and should keep the validator and the actual HTTP client in agreement about what's allowed.
- `security_logging.py` exists specifically to prevent log injection — use it for any new security-relevant log line rather than ad hoc `logging.info(f"...")` calls with untrusted data.
- Modules here should stay dependency-light (no DB session, no `Request` object) so they remain easy to unit test in isolation.

### Testing Requirements
- See `tests/test_caddyfile_utils.py`, `test_caddyfile_parser.py`, `test_admin_targets.py`, `test_hidden_captcha.py`, `test_security_logging.py`, `test_otp.py`, `test_parsing_utils.py`, `test_version_source.py`.

## Dependencies

### Internal
- Consumed by `app/services/*` and, in a few cases (e.g. `hidden_captcha.py`), directly by `app/routers/ui/auth.py`.

### External
- Mostly standard library (`re`, `ipaddress`, `hashlib`, `json`, `datetime`); `otp.py` additionally depends on `qrcode` for provisioning QR codes.

<!-- MANUAL: -->
