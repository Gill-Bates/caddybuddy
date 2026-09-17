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

## Stop a site for maintenance

The **Start/Stop** button in the **All Sites** list switches a site between serving and maintenance. A Play icon means the site is running normally; a Stop icon (together with a **Maintenance** badge) means it is stopped.

While a site is stopped, Caddy keeps serving its domains, but every request receives HTTP 503 with the maintenance page instead of reaching the site's handlers. The site's `tls`, `log`, and `bind` directives stay in place, as do imports of baseline snippets that contain a `tls` directive (for example DNS-challenge setup), so certificates keep renewing. Other imports are dropped while the site is stopped because they may contain request handlers. Starting the site deploys its normal site block again. The button is unavailable for disabled sites, which are not part of the Caddy configuration at all.

Edit the maintenance page under **Settings → General → Maintenance Page**. The editor supports headings, paragraphs, bold/italic/underline, lists, and links (`https://`, `http://`, `mailto:`); any other markup is removed when saving. Visitors see this content on a card in front of a dark, animated space scene with an astronaut at work; the scene and the "503 · Maintenance" label are fixed and not editable. **Preview** opens the full page with the current, unsaved content. Saving the page redeploys the configuration when at least one site is stopped.

## Delete a site

Deletion removes the stored site and deploys the resulting configuration. Review the confirmation dialog carefully because the active Caddy configuration changes immediately.

## Certificate renewal

Certificate-renewal actions depend on the detected certificate state. A force renewal that purges local certificate artifacts requires certificate-storage access and a working runtime control mode; CaddyBuddy then reloads Caddy and restarts it if needed. Without a control mode, artifact-purge cases fall back to a best-effort Admin API reload, while repairs that require a restart are unavailable. The reload prompts Caddy to renew only certificates that are missing or within their renewal window; a still-valid certificate is left untouched. CaddyBuddy monitors the resulting certificate state instead of assuming the action completed the renewal.
