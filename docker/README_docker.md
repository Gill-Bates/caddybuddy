<p align="center">
	<img src="https://github.com/Gill-Bates/caddybuddy/raw/main/app/static/img/caddybuddy_1c.svg" width="380" alt="CaddyBuddy logo">
</p>

<p align="center">
	<a href="https://github.com/Gill-Bates/caddybuddy/releases"><img src="https://img.shields.io/github/v/release/Gill-Bates/caddybuddy?logo=github&logoColor=white" alt="GitHub Release"></a>
	<a href="https://hub.docker.com/r/giiibates/caddybuddy"><img src="https://img.shields.io/docker/pulls/giiibates/caddybuddy?logo=docker&logoColor=white" alt="Docker Pulls"></a>
	<a href="https://hub.docker.com/r/giiibates/caddybuddy"><img src="https://img.shields.io/docker/image-size/giiibates/caddybuddy?logo=docker&logoColor=white" alt="Docker Image Size"></a>
	<br>
	<a href="https://github.com/Gill-Bates/caddybuddy/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg" alt="License"></a>
	<img src="https://img.shields.io/badge/Platform-linux%2Famd64%20%7C%20linux%2Farm64-lightgrey?logo=linux&logoColor=white" alt="Platform">
</p>

CaddyBuddy is a lightweight web UI for managing a single Caddy installation.

It provides a dashboard, site management, a Caddyfile editor, SSL monitoring, onboarding, and secure defaults in one compact container image.

## Image

```bash
docker pull giiibates/caddybuddy:latest
```

Supported platforms:

- `linux/amd64`
- `linux/arm64`

## Quick Start

Generate a strong session secret:

```bash
export CB_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
```

Then start CaddyBuddy with the example Compose file from the repository:

```bash
docker compose -f docker/docker-compose.yml.example up -d
```

The container expects access to your Caddy Admin API and a persistent data directory.

## Documentation

- GitHub: https://github.com/Gill-Bates/caddybuddy
- README: https://github.com/Gill-Bates/caddybuddy/blob/main/README.md
- Releases: https://github.com/Gill-Bates/caddybuddy/releases
