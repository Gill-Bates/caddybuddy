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
- constrained Caddy Admin API targets;
- validation before Caddy configuration deployment.

## Reverse-proxy headers

Caddy forwards the original client address, host, and scheme automatically:

```caddyfile
reverse_proxy 127.0.0.1:8000
```

Set `FORWARDED_ALLOW_IPS` to the exact IP addresses or CIDR networks of the proxies that connect to CaddyBuddy. Uvicorn then resolves `request.client` from `X-Forwarded-For`, walking a proxy chain from right to left. Incorrect trust can let clients spoof the address consumed by rate limiting and banning tools; never use `*` when untrusted clients can reach the application port directly.

## Failed-login monitoring

Every rejected login and login rate-limit response emits one single-line event to the container log:

```text
2026-09-14T15:30:00.000Z WARNING:  SECURITY authentication_failed client_ip=198.51.100.27 username="admin" reason=invalid_credentials status_code=403
```

The event name, field order, reason values, and numeric status are stable for CrowdSec and Fail2Ban parsers. IPv4 and IPv6 addresses are normalized. User-controlled values are length-limited and JSON-escaped to prevent log injection. `client_ip=unknown` is emitted instead of trusting a malformed address.

Example Fail2Ban filter for `docker logs` or a Docker systemd-journal backend:

```ini
[Definition]
failregex = ^\S+\s+WARNING:\s+SECURITY authentication_failed client_ip=<HOST> username=.* reason=(?:anti_bot_rejected|invalid_credentials|username_too_long|password_too_long|rate_limited) status_code=(?:403|429)$
ignoreregex =
```

Only configure this filter after `FORWARDED_ALLOW_IPS` identifies the complete trusted proxy chain; otherwise the logged address is the proxy rather than the originating client.

## Configuration ownership

CaddyBuddy is designed to own the active configuration after onboarding. Concurrent writers can bypass validation, overwrite generated content, or create a mismatch between the database and Caddyfile.

## Reporting vulnerabilities

Do not publish secrets or exploitable details in a public issue. Contact the repository owner through the channels listed on the [GitHub profile](https://github.com/Gill-Bates).
