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


<details>
<summary>Previous versions...</summary>

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
