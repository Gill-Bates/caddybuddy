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

CaddyBuddy provides guided onboarding, site and Caddyfile management, certificate visibility, SSL Labs assessments, and health monitoring from a server-rendered FastAPI application.

## Quick Start

```bash
export CB_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
docker compose -f docker/docker-compose.yml.example up -d
```

Open `http://127.0.0.1:8000`, create the initial administrator account, and complete the onboarding wizard.

## Documentation

Full installation, configuration, operation, and development documentation:

**https://gill-bates.github.io/caddybuddy/**

Additional links: [Docker Hub](https://hub.docker.com/r/giiibates/caddybuddy) · [Releases](https://github.com/Gill-Bates/caddybuddy/releases) · [Changelog](CHANGELOG.md)

## License

CaddyBuddy is distributed under the [GNU Affero General Public License v3.0](https://www.gnu.org/licenses/agpl-3.0.html).
