---
hide:
  - navigation
---

# CaddyBuddy

<div class="hero" markdown>

![CaddyBuddy](https://raw.githubusercontent.com/Gill-Bates/caddybuddy/main/app/static/img/caddybuddy_1c.svg)

<p class="hero__tagline">
Manage one Caddy installation through a focused web interface for sites, configuration, certificates, and SSL Labs assessments.
</p>

[Get started](getting-started/quick-start.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/Gill-Bates/caddybuddy){ .md-button }

</div>

## What CaddyBuddy manages

<div class="grid cards" markdown>

-   :material-server-network: **Caddy runtime**

    Guided onboarding verifies the Admin API and establishes CaddyBuddy as the configuration manager.

    [Connect Caddy](configuration/caddy-runtime.md)

-   :material-web: **Sites**

    Create, validate, enable, disable, and deploy site blocks with one or more domains.

    [Manage sites](features/sites.md)

-   :material-file-code: **Caddyfile**

    Edit the managed configuration, validate syntax, and deploy changes through Caddy's Admin API.

    [Use the editor](features/caddyfile.md)

-   :material-certificate: **Certificates**

    See certificate state and remaining validity for configured domains, and trigger renewals when runtime control is available.

    [Dashboard overview](features/dashboard.md)

-   :material-shield-check: **SSL Labs**

    Register an email address, run assessments, schedule weekly scans, and inspect grade history.

    [Configure SSL Labs](features/ssl-labs.md)

-   :material-lock-check: **Protected administration**

    Session authentication, CSRF validation, security headers, rate limiting, and constrained Caddy Admin targets are built in.

    [Security guidance](operations/security.md)

</div>

## Deployment model

CaddyBuddy is designed for a single Caddy installation. The recommended deployment uses the published Docker image, a persistent application data directory, access to a private Caddy Admin API, and optional read access to Caddy's certificate storage.

!!! warning "Exclusive configuration ownership"
    After onboarding, treat CaddyBuddy as the owner of the active Caddy configuration. Avoid editing the managed Caddyfile concurrently with another deployment tool.

## Useful links

- [Docker Hub](https://hub.docker.com/r/giiibates/caddybuddy)
- [GitHub releases](https://github.com/Gill-Bates/caddybuddy/releases)
- [Issue tracker](https://github.com/Gill-Bates/caddybuddy/issues)
- [Changelog](changelog.md)
