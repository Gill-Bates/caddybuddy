# 🚀 CaddyBuddy

> Modern web-based administration dashboard for managing [Caddy](https://caddyserver.com/) servers

[![Python 3.13+](https://img.shields.io/badge/Python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136+-green.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

CaddyBuddy provides a sleek, secure dashboard to manage multiple Caddy server instances, configurations, users, and API keys—all from a single interface.

---

## 📑 Table of Contents

- [Features](#-features)
- [Screenshots](#-screenshots)
- [Prerequisites](#-prerequisites)
- [Quick Start](#-quick-start)
  - [Docker (Recommended)](#docker-recommended)
  - [Local Development](#local-development)
- [Configuration](#-configuration)
- [Architecture](#-architecture)
- [API Reference](#-api-reference)
- [Security](#-security)
- [Development](#-development)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🖥️ **Multi-Server Management** | Connect and manage multiple Caddy instances |
| ⚙️ **Configuration Editor** | View, edit, and deploy Caddy configurations |
| 👥 **User Management** | Role-based access control (Admin/User) |
| 🔑 **API Keys** | Generate and manage API keys with expiration |
| 📋 **Audit Logs** | Track all actions with detailed audit trail |
| 🔒 **Security First** | CSRF protection, rate limiting, secure sessions |
| 🐳 **Docker Ready** | Production-ready container with health checks |
| 🌙 **Modern UI** | Responsive Bootstrap 5 interface |

---

## 📸 Screenshots

*Coming soon*

---

## 📋 Prerequisites

- **Python 3.13+** (for local development)
- **Docker & Docker Compose** (for containerized deployment)
- Access to one or more Caddy server admin APIs

---

## 🚀 Quick Start

### Docker (Recommended)

1. **Generate a secure secret key:**

   ```bash
   export CADDYBUDDY_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
   ```

2. **Create the data directory:**

   ```bash
   mkdir -p /opt/caddybuddy/data
   ```

3. **Create `docker-compose.yml`:**

   ```yaml
   services:
     caddybuddy:
       image: docker.cirrio.de/caddybuddy:latest
       container_name: caddybuddy
       restart: always
       ports:
         - "8000:8000"
       environment:
         CADDYBUDDY_SECRET_KEY: "${CADDYBUDDY_SECRET_KEY}"
         CADDYBUDDY_ADMIN_PASSWORD: "your-strong-password"
         LOG_LEVEL: "INFO"
         TZ: "Europe/Berlin"
       volumes:
         - ./data:/app/data
       healthcheck:
         test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)"]
         interval: 30s
         timeout: 10s
         retries: 3
   ```

4. **Start the container:**

   ```bash
   docker compose up -d
   ```

5. **Access the dashboard:** Open `http://localhost:8000` and login with `admin` / your configured password.

---

### Local Development

1. **Clone the repository:**

   ```bash
   git clone https://github.com/Gill-Bates/caddybuddy.git
   cd caddybuddy
   ```

2. **Create virtual environment:**

   ```bash
   python3.13 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Set environment variables:**

   ```bash
   export CADDYBUDDY_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
   export CADDYBUDDY_ADMIN_PASSWORD="your-dev-password"
   export LOG_LEVEL="DEBUG"
   export CADDYBUDDY_ENVIRONMENT="development"
   ```

4. **Run the server:**

   ```bash
   python run.py
   ```

5. **Access at:** `http://localhost:8000`

---

## ⚙️ Configuration

All configuration is done via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CADDYBUDDY_SECRET_KEY` | — | **Required.** Session encryption key (base64) |
| `CADDYBUDDY_ADMIN_PASSWORD` | `admin` | Initial admin password |
| `CADDYBUDDY_ENVIRONMENT` | `production` | `development`, `staging`, or `production` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `PORT` | `8000` | HTTP server port |
| `TZ` | `UTC` | Timezone (e.g., `Europe/Berlin`) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Trusted proxy IPs |

> ⚠️ **Security Note:** Always set a strong, unique `CADDYBUDDY_SECRET_KEY` and `CADDYBUDDY_ADMIN_PASSWORD` in production.

---

## 🏗️ Architecture

```
caddybuddy/
├── app/
│   ├── main.py           # FastAPI application factory
│   ├── config/           # Settings, logging, rate limiting
│   ├── database/         # SQLAlchemy async session management
│   ├── models/           # SQLAlchemy ORM entities
│   ├── repositories/     # Data access layer
│   ├── services/         # Business logic (auth, caddy, audit)
│   ├── routers/          # API & UI route handlers
│   ├── middleware/       # Security headers, CSRF
│   ├── templates/        # Jinja2 HTML templates
│   └── static/           # CSS, JS, images
├── data/                 # SQLite database (WAL mode)
├── docker/               # Dockerfile & compose files
└── run.py                # Uvicorn entrypoint
```

**Tech Stack:**

- **Backend:** FastAPI + Uvicorn + Python 3.13
- **Database:** SQLite (WAL mode) + SQLAlchemy 2.x (async)
- **Frontend:** Jinja2 + Bootstrap 5 + Vanilla JS
- **Security:** SlowAPI rate limiting, bcrypt hashing, secure sessions

---

## 📡 API Reference

CaddyBuddy provides a RESTful API at `/api/v1/`:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/health` | `GET` | Health check endpoint |
| `/api/v1/build-info` | `GET` | Application version info |

**Example:**

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok","app":"CaddyBuddy","version":"0.1"}
```

> Full API documentation available at `/docs` (Swagger UI) or `/redoc`.

---

## 🔒 Security

CaddyBuddy implements multiple layers of security:

- **Authentication:** Session-based with bcrypt password hashing
- **Authorization:** Role-based access control (Admin/User)
- **CSRF Protection:** Token validation on all state-changing requests
- **Rate Limiting:** SlowAPI-based protection against brute force
- **Security Headers:** HSTS, X-Frame-Options, CSP, X-Content-Type-Options
- **Session Security:** HttpOnly, Secure (in production), SameSite=Lax cookies

**Recommended Production Setup:**

1. Run behind a reverse proxy (Caddy, nginx, Traefik)
2. Enable HTTPS termination at the proxy level
3. Set `FORWARDED_ALLOW_IPS` to your proxy's IP
4. Use strong, unique passwords and secret keys

---

## 🛠️ Development

### Running Tests

```bash
pytest tests/ -v
```

### Code Quality

```bash
# Format
black app/ tests/
ruff check app/ tests/ --fix

# Type checking
mypy app/
```

### UI Linting

```bash
cd tools/ui-lint
npm install
UI_LINT_BASE_URL=http://localhost:8000 npm run lint
```

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

## 👤 Author

**Gill-Bates**

- GitHub: [@Gill-Bates](https://github.com/Gill-Bates)

---

<p align="center">
  Made with ❤️ for the Caddy community
</p>
