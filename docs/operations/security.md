# Security

## Deployment rules

- Put CaddyBuddy behind HTTPS.
- Keep the Caddy Admin API on loopback, a private network, or an internal Docker network.
- Bind the application port to loopback unless direct network access is intentional.
- Persist and protect the `data` directory because it contains the database and application state.
- Use a unique random `CB_SECRET_KEY` of at least 32 characters.
- Restrict `FORWARDED_ALLOW_IPS` to trusted reverse proxies.
- Keep `SESSION_HTTPS_ONLY=true` in production.

## Application protections

CaddyBuddy includes:

- authenticated sessions with inactivity and absolute timeouts;
- CSRF tokens and origin validation for state-changing browser requests;
- an invisible honeypot plus a signed, time-bound token on public authentication forms;
- security response headers;
- rate limiting for sensitive endpoints;
- password complexity validation;
- optional passkey (WebAuthn) sign-in as an alternative to a password;
- optional TOTP two-factor authentication with encrypted secrets, replay protection, and one-time recovery codes;
- constrained Caddy Admin API targets;
- validation before Caddy configuration deployment.

## Passkeys

Passkeys are enrolled per account under **Settings → Passkey**, up to 10 per account, each with an optional device name. Enrollment and deletion require the current password. A registered passkey can then be used to sign in from the login page instead of a password, using the platform authenticator or a security key via `navigator.credentials`. The passkey button only appears on the login page once at least one passkey is registered.

- A passkey replaces the password only. If the account has two-factor authentication enabled, the TOTP prompt still follows a successful passkey sign-in.
- Password sign-in always remains available. Removing a passkey — including the last one — cannot lock the account out.
- Registration is rate limited to 10 requests per minute. Sign-in is rate limited to 10 challenge requests per minute (60 per hour) and 5 verification attempts per minute (20 per hour). Failed verifications are logged with `reason=invalid_passkey`.

Registration and sign-in each run as a short-lived ceremony: the server issues a one-time challenge that expires after five minutes, persisted in the database rather than in the session or process memory, so it survives a worker switch; the challenge is consumed atomically and cannot be replayed.

Ceremonies are bound to a Relying Party ID (a bare hostname) and an expected origin, taken from `CB_PASSKEY_RP_ID` / `CB_PUBLIC_ORIGIN` when configured, otherwise derived from the request. Passkeys are bound to the RP ID, so changing the deployment hostname invalidates every registered credential — set `CB_PASSKEY_RP_ID` explicitly if the deployment hostname can change.

## Two-factor authentication

Two-factor authentication is enabled per account under **Settings → Security**. Enabling and disabling both require the current password. Setup shows a QR code, the Base32 secret, and the provisioning URI; the secret becomes active only after a valid code from the authenticator app confirms it. CaddyBuddy then shows eight single-use recovery codes exactly once.

- Codes are standard RFC 6238 TOTP values (SHA-1, six digits, 30-second period). Only the current time step is accepted, so the server clock must be synchronized (NTP).
- A code cannot be reused: each accepted time step is recorded per account.
- After a correct password, the second-factor prompt stays valid for three minutes and is rate limited to 5 attempts per minute and 20 per hour. Wrong codes are logged with `reason=invalid_otp`.
- Enabling, confirming, or disabling two-factor authentication and changing the password invalidate all other sessions of that account.

TOTP secrets are encrypted and recovery codes are hashed with keys derived from `PASSWORD_PEPPER` (falling back to `CB_SECRET_KEY` when no pepper is configured). The same effective value also protects password hashes. Changing it makes existing password hashes, TOTP secrets, and recovery codes unusable; disabling two-factor authentication alone does not preserve password sign-in. Do not rotate it in place without a credential migration or password-reset plan for every affected account.

If both the authenticator and all recovery codes are lost, stop the container, back up the Compose example's `docker/data/caddybuddy.db`, and reset the factor directly in the database:

```bash
sqlite3 docker/data/caddybuddy.db "UPDATE users SET otp_enabled = 0, otp_secret = NULL, otp_recovery_codes = NULL, otp_last_verified_counter = NULL WHERE username = 'admin';"
```

Sign in with the password afterwards and enroll again.

## Reverse-proxy headers

Caddy forwards the original client address, host, and scheme automatically:

```caddyfile
reverse_proxy 127.0.0.1:8000
```

Set `FORWARDED_ALLOW_IPS` to the exact IP addresses or CIDR networks of the proxies that connect to CaddyBuddy. Uvicorn then resolves `request.client` from `X-Forwarded-For`, walking a proxy chain from right to left. Incorrect trust can let clients spoof the address consumed by rate limiting and banning tools; never use `*` when untrusted clients can reach the application port directly.

## Failed-login monitoring

Rejected password, passkey, and OTP login attempts emit one single-line event to the container log. Password and OTP login rate-limit responses emit the same event:

```text
2026-09-14T15:30:00.000Z WARNING:  SECURITY authentication_failed client_ip=198.51.100.27 username="admin" reason=invalid_credentials status_code=403
```

The event name, field order, reason values, and numeric status are stable for CrowdSec and Fail2Ban parsers. IPv4 and IPv6 addresses are normalized. User-controlled values are length-limited and JSON-escaped to prevent log injection. `client_ip=unknown` is emitted instead of trusting a malformed address.

Example Fail2Ban filter for `docker logs` or a Docker systemd-journal backend:

```ini
[Definition]
failregex = ^\S+\s+WARNING:\s+SECURITY authentication_failed client_ip=<HOST> username=.* reason=(?:anti_bot_rejected|invalid_credentials|invalid_otp|invalid_passkey|username_too_long|password_too_long|rate_limited) status_code=(?:400|401|403|429)$
ignoreregex =
```

Only configure this filter after `FORWARDED_ALLOW_IPS` identifies the complete trusted proxy chain; otherwise the logged address is the proxy rather than the originating client.

## Configuration ownership

CaddyBuddy is designed to own the active configuration after onboarding. Concurrent writers can bypass validation, overwrite generated content, or create a mismatch between the database and Caddyfile.

## Reporting vulnerabilities

Do not publish secrets or exploitable details in a public issue. Contact the repository owner through the channels listed on the [GitHub profile](https://github.com/Gill-Bates).
