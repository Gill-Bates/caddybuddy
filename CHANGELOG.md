## [1.5.0] - 2026-09-14

- `New` Added an About page with version information, release notes, and GitHub update checks.
- `New` Added monthly SSL Labs scans alongside weekly scheduling, with clearer endpoint grades and scheduling controls.
- `New` Added a best-effort certificate renewal request through the Caddy Admin API for installations without a runtime control mode. Caddy renews only certificates that are missing or within their renewal window.
- `New` Added an "Unlimited" option to the SSL Labs history retention slider, alongside finer-grained day steps (7, 14, 30, 90, 180, 365).
- `New` Relicensed under the MIT License (previously GNU AGPL-3.0).
- `Fix` Improved certificate renewal reliability, verification across all site domains, and error reporting for unavailable storage and failed Caddy operations.
- `Fix` Improved certificate status accuracy and refreshed results after renewal.
- `Fix` Improved responsive layouts for sites, SSL Labs, settings, and the Caddyfile editor.
- `Fix` The default Caddy logging configuration now writes to a rotating file instead of stdout only, preventing unbounded log growth.
- `Fix` Docker certificate-storage permission repair now fixes ownership per file instead of only the top directory, verifies a sample certificate is actually readable after repair, and reports clear errors when ACLs cannot be applied.
- `Security` Remote certificate checks now verify TLS trust and hostname coverage before reporting a certificate as valid.
- `Security` Hardened certificate cleanup and renewal planning to prevent affecting unrelated certificate and ACME account files.
- `Security` The Caddy Admin API client no longer honors proxy environment variables, closing a potential path for redirecting its internal requests.
- `Security` Docker certificate-storage ACL repair now grants read access only to `.crt` certificate files, excluding private keys and ACME account metadata; the storage path is also canonicalized before validation to prevent unsafe paths (e.g. containing `..`) from being accepted.
- `Security` Failed login attempts are now logged in a structured, parseable format (client IP, reason, status code) suitable for external banning tools such as fail2ban.
- `Security` The example Docker Compose file no longer trusts all proxy IPs by default; `FORWARDED_ALLOW_IPS` now defaults to `127.0.0.1` unless explicitly overridden.


<details markdown="1">
<summary>Previous versions...</summary>

## [1.4] - 2026-06-15

- `New` Added browser-based first-run setup so a fresh instance can create the initial admin account without container-only bootstrap steps.
- `New` Added a 4-step Caddy onboarding wizard: select runtime location, choose configuration source, verify Admin API and file access, then review and execute. Includes per-field status indicators, inline assisted Admin API enablement, and a confetti celebration on successful completion.
- `New` Added a richer Caddyfile editor with syntax highlighting, autocomplete, brace diagnostics, formatting help, and a safer deploy flow.
- `New` Added configurable Caddy control modes for renewals and restarts, covering systemd, Docker, and custom script execution.
- `New` Added an SSL Labs rank history chart to the dashboard with selectable time range (7 d – 1 y), per-domain focus view, and weekly grade samples.
- `New` Added monthly scheduling, retention controls, and inline actions to the SSL Labs page.
- `New` Improved certificate management with better wildcard detection, renewal guidance, artifact cleanup, and clearer site status labels.
- `Fix` Improved onboarding reliability when Caddy is missing, the Admin API is disabled, or the managed Caddyfile is not ready yet.
- `Fix` Improved Caddyfile deployment and rollback behavior, including restoring the previous config after failed admin loads.
- `Fix` Improved startup reconciliation so Caddy configuration, database state, and dashboard/status views stay in sync more reliably.
- `Fix` Improved UI consistency across dashboard, login, settings, onboarding, sites, and SSL Labs pages.
- `Security` Hardened Caddy Admin API target validation to allow only safe local/private addresses with explicit ports and no embedded credentials.
- `Security` Hardened session, CSRF, redirect, and security-header handling; CSP nonce now covers both inline styles and inline scripts.
- `Security` All JavaScript assets are served from local static files — no external CDN requests at runtime.
- `Security` Restricted control commands, script execution, and certificate cleanup to validated paths and permission checks.
- `Security` Reduced exposure of internal error details in UI responses and event streams.

## [1.3] - 2026-06-11

- `New` SSL Labs: Enabling a scan scheduler (weekly/monthly) automatically triggers a scan if no current result is available
- `New` SSL Labs: Report button is shown only while a scan result is still available in cache (80-hour window)
- `New` Caddy configuration is automatically reconciled with the database on startup
- `New` Caddyfile changes are written atomically (temp file + rename, no partial writes on crash)
- `New` Certificate renewal: improved error text and hint when Caddy manages the certificate internally
- `Fix` Configuration file path can now only point to allowed directories (/app, /etc/caddy, /config)
- `Fix` SSL Labs registration status now exposes the email address only in masked form
- `Fix` chmod hint for the sites directory now shows the correct group/ACL recommendation
- `Security` Minimum length for secret key (32 characters) and admin password (12 characters) is enforced
- `Security` Bcrypt cost and user roles are validated when creating users
- `Security` Forwarded-For wildcard (`*`) in proxy settings is rejected

## [1.2] - 2026-05-29

- ``New`` Certificate renewal button added to Sites actions for forcing certificate re-issuance
- ``New`` Real-time certificate renewal progress via SSE - shows "Renewing..." spinner during renewal
- ``New`` Validate button now auto-formats Caddyfile and site directives using Caddy's built-in formatter
- ``Fix`` Creating a new site now redirects back to /sites instead of the site detail page
- ``Fix`` SSL Labs "Report" button only shows when a scan completed successfully with a grade
- ``Fix`` Removed redundant "Primary domain" label from certificate status display
- ``Fix`` Footer now stays at the bottom of the page (sticky footer)
- ``Fix`` SSL Labs schedule dropdown alignment for unscanned domains in desktop view

## [1.1] - 2026-05-28
- ``New`` Adding a new Logo
- ``Fix`` Several Design improvements
- ``Fix`` Switch from Banner to Toast notification

## [1.0] - 2026-05-28

- ``New`` Initial Release

</details>
