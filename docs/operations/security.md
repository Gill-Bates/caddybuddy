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
- security response headers;
- rate limiting for sensitive endpoints;
- password complexity validation;
- constrained Caddy Admin API targets;
- validation before Caddy configuration deployment.

## Reverse-proxy headers

Forward the original host and scheme:

```caddyfile
reverse_proxy 127.0.0.1:8000 {
    header_up Host {host}
    header_up X-Forwarded-Proto {scheme}
}
```

Incorrect forwarded-header trust can allow clients to spoof request metadata. Trust only the proxy addresses that actually connect to CaddyBuddy.

## Configuration ownership

CaddyBuddy is designed to own the active configuration after onboarding. Concurrent writers can bypass validation, overwrite generated content, or create a mismatch between the database and Caddyfile.

## Reporting vulnerabilities

Do not publish secrets or exploitable details in a public issue. Contact the repository owner through the channels listed on the [GitHub profile](https://github.com/Gill-Bates).
