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

## Caddy does not start after a reboot

`systemctl status caddy` shows a failed start, and a manual `systemctl restart caddy` fixes it:

```text
Error: loading initial config: loading new config: starting caddy administration endpoint: listen tcp 10.30.0.1:2019: bind: cannot assign requested address
```

CaddyBuddy writes the host from the Caddy Admin API URL into the managed Caddyfile as `admin <host>:<port>`. If that host is a non-loopback IP on a bridge, VPN, or container network interface, systemd can start Caddy before the interface has its address. Caddy then cannot bind the Admin API and exits.

Find the interface that owns the address:

```bash
ip -br addr | grep 10.30.0.1
```

Add a drop-in with `systemctl edit caddy` and replace `cloudnet0` with that interface name:

```ini
[Unit]
Wants=sys-subsystem-net-devices-cloudnet0.device
After=sys-subsystem-net-devices-cloudnet0.device

[Service]
Restart=on-failure
RestartSec=5s
```

Then run `systemctl daemon-reload`. The device ordering waits for the interface, and `Restart=on-failure` covers the short gap until its address is assigned. If a known service creates the interface, such as `docker.service`, `wg-quick@wg0.service`, or `incus.service`, you can also add it to `After=`.

As an alternative, allow binding to addresses that do not exist yet:

```bash
echo 'net.ipv4.ip_nonlocal_bind = 1' | sudo tee /etc/sysctl.d/99-nonlocal-bind.conf
sudo sysctl --system
```

Do not bind the Admin API to `0.0.0.0` to work around this, because that can expose it on public interfaces.

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
