# Caddyfile Editor

The Caddyfile page exposes the managed global configuration assembled by CaddyBuddy.

## Validate before deployment

The editor validates Caddyfile syntax before loading changes into Caddy. Invalid input is rejected without replacing the active runtime configuration.

## Managed content

The generated configuration combines:

- the global options block;
- reusable snippets;
- enabled site blocks stored in CaddyBuddy.

The global ACME email and Caddy Admin endpoint are managed settings. Site blocks should be edited through **Sites** when possible so the database and generated file stay consistent.

## Default runtime logging

Fresh configurations include central Caddy runtime logging:

```caddyfile
log {
    level info
    format json
    output file /var/log/caddy/runtime.json {
        roll_size 10MiB
        roll_keep 5
        roll_keep_for 168h
    }
}
```

This is global runtime logging, not per-site access logging. Add site-specific `log` directives only when individual access logs are required.

## Safe editing

!!! warning
    Do not edit the mounted Caddyfile concurrently outside CaddyBuddy. External writes can be overwritten by the next deployment and can leave stored site state out of sync with the file.
