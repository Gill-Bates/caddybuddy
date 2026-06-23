# Troubleshooting

## The container does not start

Check the logs:

```bash
docker compose -f docker/docker-compose.yml.example logs caddybuddy
```

Verify that `CB_SECRET_KEY` is set and sufficiently long, the `data` directory is writable, and `/etc/caddy/Caddyfile` is a file rather than a directory.

## Readiness returns 503

```bash
curl -i http://127.0.0.1:8000/api/v1/ready
```

Common causes:

- onboarding has not completed;
- the Caddy Admin API address is wrong;
- the API is not reachable from the container;
- Caddy is not running;
- the Admin API is bound only to a namespace that CaddyBuddy cannot access.

For a host Caddy instance from Docker, try `http://host.docker.internal:2019` and keep the `host-gateway` mapping from the example Compose file.

## The Caddyfile mount is a directory

Stop the container, remove the mistakenly created directory, create the file, and start again:

```bash
docker compose -f docker/docker-compose.yml.example down
sudo rm -rf /etc/caddy/Caddyfile
sudo install -m 0644 /dev/null /etc/caddy/Caddyfile
docker compose -f docker/docker-compose.yml.example up -d
```

Review the file path before running `rm -rf`.

## Certificate data is missing

Confirm that Caddy's storage directory is mounted into CaddyBuddy and that the container user can read it. Custom Caddy storage locations require the matching `CB_CADDY_CERTIFICATES_PATH`.

## SSL Labs scans fail

The domain must be a public hostname with a publicly reachable HTTPS service. Private addresses, local names, and URLs are rejected. Also verify the SSL Labs registration status under **Settings**.

## Login or form submissions fail behind HTTPS

Ensure the reverse proxy sends `X-Forwarded-Proto` and that `FORWARDED_ALLOW_IPS` trusts the proxy's actual source address. Do not trust arbitrary public clients.
