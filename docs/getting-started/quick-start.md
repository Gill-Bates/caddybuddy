# Quick Start

The repository includes a Compose example for the published multi-architecture image.

## Prerequisites

- Linux on `amd64` or `arm64`
- Docker Engine with Docker Compose
- A running Caddy 2 installation, or a Caddy installation that will be connected later
- A private network path from CaddyBuddy to the Caddy Admin API

## Start CaddyBuddy

1. Clone the repository:

    ```bash
    git clone https://github.com/Gill-Bates/caddybuddy.git
    cd caddybuddy
    ```

2. Generate a strong session secret:

    ```bash
    export CB_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
    ```

3. Ensure the paths used by the Compose example exist:

    ```bash
    mkdir -p data
    sudo test -f /etc/caddy/Caddyfile
    ```

4. Start the application:

    ```bash
    docker compose -f docker/docker-compose.yml.example up -d
    ```

5. Open `http://127.0.0.1:8000`.

## Complete the first run

An empty database opens the account setup screen. Create the `admin` account with a password containing at least eight characters, uppercase and lowercase letters, a digit, and a special character.

The onboarding wizard then:

1. identifies where Caddy runs;
2. chooses whether to import an existing configuration or start from the baseline;
3. verifies Admin API and file access;
4. shows the takeover summary before execution.

Continue with [First Run](first-run.md) for the onboarding details.

## Verify the service

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/ready
```

`/health` confirms that the web process is running. `/ready` also requires completed onboarding and a reachable Caddy Admin API.
