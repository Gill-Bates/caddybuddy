# Caddy Runtime

## Admin API

CaddyBuddy deploys configuration through Caddy's Admin API. The API must be reachable from the application but must not be exposed publicly.

Supported target names are intentionally constrained to local and deployment-internal hosts such as `localhost`, `host.docker.internal`, and `caddy`, plus private or internal addresses accepted by validation.

Typical Docker-to-host configuration:

```text
http://host.docker.internal:2019
```

The example Compose file maps `host.docker.internal` through Docker's `host-gateway`.

## Caddyfile

The default container path is:

```text
/app/Caddyfile
```

The mounted path must point to a file named `Caddyfile`. It must be writable when CaddyBuddy imports, backs up, or replaces an existing host configuration.

## Certificate storage

Mount Caddy's storage directory when certificate inspection and renewal monitoring are required:

```yaml
volumes:
  - /var/lib/caddy/.local/share/caddy:/var/lib/caddy/.local/share/caddy
```

## Runtime control modes

CaddyBuddy can operate without direct service control, or use:

- `systemd` for a host unit;
- `docker` for a named container;
- `script` for a deployment-specific control command.

Runtime control is separate from configuration loading through the Admin API. Keep it disabled unless restart or renewal workflows require it.

## Trusted proxies

The baseline Caddyfile contains a disabled trusted-proxy example. Enable it only when Caddy runs behind a trusted proxy or CDN:

```caddyfile
servers {
    trusted_proxies static private_ranges
    trusted_proxies_strict
}
```
