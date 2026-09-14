<p align="center">
	<img src="https://github.com/Gill-Bates/caddybuddy/raw/main/app/static/img/caddybuddy_1c.svg" width="380" alt="CaddyBuddy logo">
</p>

<p align="center">
	<a href="https://github.com/Gill-Bates/caddybuddy/releases"><img src="https://img.shields.io/github/v/release/Gill-Bates/caddybuddy?logo=github&logoColor=white" alt="GitHub Release"></a>
	<a href="https://hub.docker.com/r/giiibates/caddybuddy"><img src="https://img.shields.io/docker/pulls/giiibates/caddybuddy?logo=docker&logoColor=white" alt="Docker Pulls"></a>
	<a href="https://hub.docker.com/r/giiibates/caddybuddy"><img src="https://img.shields.io/docker/image-size/giiibates/caddybuddy?logo=docker&logoColor=white" alt="Docker Image Size"></a>
	<br>
	<a href="https://github.com/Gill-Bates/caddybuddy/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License"></a>
	<img src="https://img.shields.io/badge/Platform-linux%2Famd64%20%7C%20linux%2Farm64-lightgrey?logo=linux&logoColor=white" alt="Platform">
</p>

CaddyBuddy is a lightweight web UI for managing a single Caddy installation.

It provides a dashboard, site management, a Caddyfile editor, certificate monitoring and renewal, SSL Labs assessments with weekly or monthly scheduling, onboarding, and secure defaults in one compact container image.

## Screenshots

<p align="center">
  <img src="https://github.com/Gill-Bates/caddybuddy/raw/main/.github/img/screen_1.jpeg" alt="Sign In" width="80%" style="border-radius: 6px; margin-bottom: 16px;">
</p>
<p align="center">
  <img src="https://github.com/Gill-Bates/caddybuddy/raw/main/.github/img/screen_2.jpeg" alt="Dashboard" width="80%" style="border-radius: 6px; margin-bottom: 16px;">
</p>
<p align="center">
  <img src="https://github.com/Gill-Bates/caddybuddy/raw/main/.github/img/screen_3.jpeg" alt="SSL Labs rank history" width="80%" style="border-radius: 6px;">
</p>

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
CADDYBUDDY_VERSION=latest docker compose -f docker/docker-compose.yml.example up -d
```

The container expects access to your Caddy Admin API, a persistent data directory,
and Caddy's certificate storage mounted at `/var/lib/caddy/.local/share/caddy`
for certificate inspection and renewal. Without a configured Caddy control mode,
CaddyBuddy can request a reload through the Admin API; a full forced renewal or
repair may still require runtime control.

## Documentation

- Documentation: https://gill-bates.github.io/caddybuddy/
- GitHub: https://github.com/Gill-Bates/caddybuddy
- Releases: https://github.com/Gill-Bates/caddybuddy/releases

<br>
<p align="center">
  <a href="https://www.buymeacoffee.com/tnsteinerx">
    <img src="https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20beer&emoji=%F0%9F%8D%BA&slug=tnsteinerx&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff" alt="Buy Me A Coffee">
  </a>
</p>