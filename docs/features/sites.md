# Sites

The Sites page manages Caddy site blocks and their deployment state.

## Create a site

Provide:

- a descriptive site name;
- one or more domains;
- the Caddy directives that belong inside the site block;
- whether the site is enabled.

Use **Validate** before saving to check the generated configuration. **Create & Deploy** stores the site and loads the updated Caddy configuration.

## Multiple domains

A site can contain multiple normalized domains. Certificate state is displayed for the configured names when Caddy's certificate storage is mounted.

## Edit or disable a site

Editing and deploying replaces the generated site block. Disabling a site keeps its definition in CaddyBuddy but omits it from the active configuration.

## Delete a site

Deletion removes the stored site and deploys the resulting configuration. Review the confirmation dialog carefully because the active Caddy configuration changes immediately.

## Certificate renewal

Certificate-renewal actions depend on the detected certificate state. A force renewal that purges local certificate artifacts requires certificate-storage access and a working runtime control mode; CaddyBuddy then reloads Caddy and restarts it if needed. Without a control mode, artifact-purge cases fall back to a best-effort Admin API reload, while repairs that require a restart are unavailable. The reload prompts Caddy to renew only certificates that are missing or within their renewal window; a still-valid certificate is left untouched. CaddyBuddy monitors the resulting certificate state instead of assuming the action completed the renewal.
