<p align="center">
  <img src="app/static/img/caddybuddy_1c.svg" width="400" alt="CaddyBuddy logo">
</p>

<p align="center">
  A focused web control plane for one Caddy installation.
</p>

<p align="center">
  <a href="https://github.com/Gill-Bates/caddybuddy/releases"><img src="https://img.shields.io/github/v/release/Gill-Bates/caddybuddy?logo=github&logoColor=white" alt="GitHub Release"></a>
  <a href="https://hub.docker.com/r/giiibates/caddybuddy"><img src="https://img.shields.io/docker/pulls/giiibates/caddybuddy?logo=docker&logoColor=white" alt="Docker Pulls"></a>
  <a href="https://gill-bates.github.io/caddybuddy/"><img src="https://img.shields.io/badge/docs-GitHub%20Pages-0f766e?logo=materialformkdocs&logoColor=white" alt="Documentation"></a>
  <img src="https://img.shields.io/badge/platform-linux%2Famd64%20%7C%20linux%2Farm64-lightgrey?logo=linux&logoColor=white" alt="Platform">
</p>

CaddyBuddy provides guided onboarding, site and Caddyfile management, certificate visibility and renewal, weekly or monthly SSL Labs assessments, and health monitoring from a server-rendered FastAPI application.

## Screenshots

<p align="center">
  <img src=".github/img/screen_1.jpeg" alt="Sign In" width="80%" style="border-radius: 6px; margin-bottom: 16px;">
</p>
<p align="center">
  <img src=".github/img/screen_2.jpeg" alt="Dashboard" width="80%" style="border-radius: 6px; margin-bottom: 16px;">
</p>
<p align="center">
  <img src=".github/img/screen_3.jpeg" alt="SSL Labs rank history" width="80%" style="border-radius: 6px;">
</p>

## Quick Start

```bash
export CB_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
CADDYBUDDY_VERSION=latest docker compose -f docker/docker-compose.yml.example up -d
```

Open `http://127.0.0.1:8000`, create the initial administrator account, and complete the onboarding wizard.

## Documentation

Full installation, configuration, operation, and development documentation:

**https://gill-bates.github.io/caddybuddy/**

Additional links: [Docker Hub](https://hub.docker.com/r/giiibates/caddybuddy) · [Releases](https://github.com/Gill-Bates/caddybuddy/releases) · [Changelog](CHANGELOG.md)

## License

CaddyBuddy is distributed under the [MIT License](LICENSE).

<p align="center">
  <a href="https://www.buymeacoffee.com/tnsteinerx">
    <img src="https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20beer&emoji=%F0%9F%8D%BA&slug=tnsteinerx&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff" alt="Buy Me A Coffee">
  </a>
</p>