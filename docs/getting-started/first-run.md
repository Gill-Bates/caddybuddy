# First Run

## Administrator setup

On the first start with an empty database, CaddyBuddy asks you to create the administrator password in the browser. The username is `admin`.

The password must include:

- at least eight characters;
- an uppercase letter;
- a lowercase letter;
- a digit;
- a special character.

## Onboarding modes

The wizard supports Caddy running on the same host, in Docker, or being prepared for later installation. Available actions depend on whether CaddyBuddy can reach the Admin API and read or write the configured Caddyfile.

### Existing configuration

When importing an existing Caddyfile, CaddyBuddy checks the file, creates a backup during takeover, and switches management to its generated configuration.

### Fresh baseline

The default baseline includes:

- global JSON runtime logging with rotation;
- reusable standard and strict security-header snippets;
- the global ACME email once configured.

### Admin API repair

For supported host deployments, the wizard can add the Admin API directive to the Caddyfile and restart Caddy after explicit confirmation.

!!! danger "Configuration ownership"
    The final wizard step confirms that CaddyBuddy becomes the exclusive manager of the active Caddy configuration. Keep other automation from writing to the same Caddyfile.

## After onboarding

Use **Settings** to configure the Caddy Admin URL, mounted Caddyfile path, rate limiting, SSL Labs registration, and rank-history retention.

Then create the first site under **Sites** or review the generated configuration under **Caddyfile**.
