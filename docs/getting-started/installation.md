# Installation

## Docker Compose

Docker is the recommended production path. The example file at `docker/docker-compose.yml.example` publishes CaddyBuddy only on the host loopback interface:

```yaml
ports:
  - "127.0.0.1:8000:8000"
```

It mounts:

| Host path | Container path | Purpose |
| --- | --- | --- |
| `/etc/caddy/Caddyfile` | `/app/Caddyfile` | Managed Caddyfile |
| `./data` | `/app/data` | SQLite database and runtime state |
| `/var/lib/caddy/.local/share/caddy` | same path | Certificate inspection and renewal monitoring |

Pull and start the image:

```bash
export CB_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
docker compose -f docker/docker-compose.yml.example pull
docker compose -f docker/docker-compose.yml.example up -d
```

!!! warning "Create bind-mounted files first"
    Docker creates a directory when a bind-mounted source file does not exist. Ensure `/etc/caddy/Caddyfile` is an existing file before starting the container.

## Reverse proxy

Expose CaddyBuddy through HTTPS and preserve the original scheme:

```caddyfile
caddybuddy.example.com {
    reverse_proxy 127.0.0.1:8000 {
        header_up Host {host}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

Set `FORWARDED_ALLOW_IPS` to the address or network of the trusted reverse proxy. The Compose example uses `"*"` as an explicit deployment placeholder; narrow it in production.

## Local development

```bash
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

export CB_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
export LOG_LEVEL=DEBUG
python run.py
```

The development server listens on `http://127.0.0.1:8000` by default.

## Upgrade

For image-based deployments:

```bash
docker compose -f docker/docker-compose.yml.example pull
docker compose -f docker/docker-compose.yml.example up -d --force-recreate
```

Persist and back up the `data` directory before upgrading.
