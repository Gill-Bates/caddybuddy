# Dashboard

The dashboard provides a compact operational view of the connected Caddy installation.

## Runtime state

It reports:

- Caddy service status;
- detected Caddy version;
- service uptime;
- configured and enabled domain counts;
- valid, expired, and expiring certificate counts.

The page receives resource updates through a server-sent events stream and refreshes metrics without requiring a full page reload.

## SSL Labs rank history

When SSL Labs is configured, the dashboard shows grade distribution and rank history for assessed domains. Available time ranges depend on the retained history.

The retention setting is managed under **Settings → SSL Labs History Retention**. Older samples are removed automatically.

## Certificate data

Certificate status is derived from Caddy's mounted certificate storage. If the storage path is unavailable, CaddyBuddy continues to manage sites but cannot provide complete certificate information.
