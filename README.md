# CaddyBuddy

> Moderne Verwaltungsoberflaeche fuer Caddy-Instanzen, Domains, Konfigurationen und Zugriffe.

[![Python 3.13](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)](https://www.docker.com/)
[![SQLite](https://img.shields.io/badge/SQLite-WAL-003B57.svg)](https://www.sqlite.org/)

CaddyBuddy buendelt die wichtigsten Verwaltungsaufgaben fuer eine moderne Caddy-Umgebung in einer zentralen Weboberflaeche. Das Projekt kombiniert ein serverseitig gerendertes UI mit FastAPI, einer asynchronen SQLite-Anbindung, Echtzeit-Events per SSE und sicheren Standardeinstellungen fuer Sessions, Rate Limits und Security Header.

## Inhaltsverzeichnis

- [Ueberblick](#ueberblick)
- [Funktionen](#funktionen)
- [Tech-Stack](#tech-stack)
- [Schnellstart](#schnellstart)
- [Konfiguration](#konfiguration)
- [API und Echtzeit](#api-und-echtzeit)
- [Projektstruktur](#projektstruktur)
- [Qualitaet und Tooling](#qualitaet-und-tooling)
- [Deployment-Hinweise](#deployment-hinweise)

## Ueberblick

CaddyBuddy ist fuer Teams gedacht, die mehrere Caddy-Server, deren Konfigurationen und die zugehoerigen Verwaltungsdaten an einer Stelle pflegen wollen. Neben der UI fuer Server, Konfigurationen, Domains, API-Keys, Benutzer und Audit-Logs stellt die Anwendung auch Health- und Build-Informationen ueber eine kleine API bereit.

Die Anwendung startet bei leerer Datenbank automatisch mit einem initialen Admin-Benutzer. In Entwicklungsumgebungen laeuft sie bequem lokal, fuer produktive Szenarien steht ein Docker-Build mit Healthcheck, Build-Metadaten und persistentem Datenverzeichnis bereit.

## Funktionen

- ⚙️ Verwaltung mehrerer Caddy-Server inklusive Metadaten und Statusbezug.
- 🌐 Domain-Verwaltung mit Upstream, SSL-Provider und Server-Zuordnung.
- 🧩 Pflege von Caddy-Konfigurationen ueber das Web-UI.
- 👥 Benutzer- und Rollenverwaltung fuer Admin- und regulaere Benutzerkonten.
- 🔑 Erstellung und Verwaltung von API-Keys.
- 🧾 Audit-Logs fuer sicherheitsrelevante und administrative Aktionen.
- 📡 Echtzeit-Updates ueber Server-Sent Events unter `/api/v1/events`.
- 🔒 Sicherheitsfunktionen wie Session-Schutz, CSRF-Validierung, Security Header und Rate Limiting.
- 🐳 Docker-Image mit eingebetteter Versions- und Commit-Information aus `VERSION` und `BUILD_INFO`.

## Tech-Stack

| Bereich | Technologie |
| --- | --- |
| Backend | Python 3.13, FastAPI, Uvicorn |
| Datenbank | SQLite im WAL-Modus, SQLAlchemy 2.x, aiosqlite |
| Frontend | Jinja2, Bootstrap 5, Vanilla JavaScript |
| Sicherheit | SlowAPI, SessionMiddleware, Security Headers |
| Echtzeit | Server-Sent Events |
| Tooling | Docker, Buildx, Playwright-basierter UI-Lint |

## Schnellstart

### Lokal entwickeln

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

export CADDYBUDDY_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
export CADDYBUDDY_ADMIN_PASSWORD="LocalDevAdminPassword-Change-Me"
export CADDYBUDDY_RELOAD=1
export CADDYBUDDY_SESSION_HTTPS_ONLY=false
export LOG_LEVEL=DEBUG
export TZ=Europe/Berlin

python run.py
```

Danach ist die Anwendung standardmaessig unter `http://127.0.0.1:8000` erreichbar.

### Docker lokal bauen und starten

```bash
mkdir -p data
printf '%s\n' "dev" > BUILD_INFO

docker build -f docker/Dockerfile -t caddybuddy:local .

docker run --rm \
  -p 8000:8000 \
  -e CADDYBUDDY_SECRET_KEY="$(head -c 32 /dev/urandom | base64)" \
  -e CADDYBUDDY_ADMIN_PASSWORD="LocalDockerAdminPassword-Change-Me" \
  -e CADDYBUDDY_SESSION_HTTPS_ONLY=false \
  -e TZ=Europe/Berlin \
  -v "$PWD/data:/app/data" \
  caddybuddy:local
```

Wenn du ein Release-Image bauen willst, sollten `VERSION` und `BUILD_INFO` vor dem Build passend gesetzt sein.

## Konfiguration

Die wichtigsten Einstellungen werden ueber Umgebungsvariablen gesteuert:

| Variable | Beispiel | Beschreibung |
| --- | --- | --- |
| `CADDYBUDDY_RELOAD` | `true` | Aktiviert den Uvicorn-Reload-Modus fuer lokale Entwicklung. |
| `CADDYBUDDY_ALLOW_INSECURE_DEFAULTS` | `true` | Erlaubt unsichere Defaults nur bewusst fuer disposable lokale Setups. |
| `CADDYBUDDY_SECRET_KEY` | Base64-String | Pflicht; signiert und schuetzt Sessions. |
| `CADDYBUDDY_ADMIN_PASSWORD` | `replace-me` | Passwort fuer den initialen Admin-Benutzer. |
| `CADDYBUDDY_SESSION_HTTPS_ONLY` | `false` | Schaltet `Secure`-Cookies fuer lokale HTTP-Tests aus. |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/app.db` | Verbindungszeichenfolge fuer die Datenbank. |
| `LOG_LEVEL` | `INFO` | Logging-Level fuer Uvicorn und Anwendung. |
| `TZ` | `Europe/Berlin` | IANA-Zeitzone. |
| `CADDYBUDDY_PORT` oder `PORT` | `8000` | HTTP-Port der Anwendung. |
| `CADDYBUDDY_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Vertrauenswuerdige Proxy-IPs fuer Forwarded Headers. |

Wichtig:

- Die App erzwingt immer ein starkes Secret. Das Default-Admin-Passwort wird erst dann geprueft, wenn bei leerer Datenbank wirklich ein initialer Admin angelegt werden muss.
- Fuer lokale HTTP-Tests `CADDYBUDDY_SESSION_HTTPS_ONLY=false` setzen, damit Session-Cookies auch ohne HTTPS funktionieren.
- Fuer Wegwerf-Setups kannst du alternativ explizit `CADDYBUDDY_ALLOW_INSECURE_DEFAULTS=true` setzen, statt ein starkes Initialpasswort zu vergeben.
- Das persistente SQLite-File liegt standardmaessig unter `data/app.db`.

## API und Echtzeit

Die System-API ist klein, aber praktisch fuer Monitoring und Integrationen:

| Endpoint | Zweck |
| --- | --- |
| `GET /api/v1/health` | Healthcheck mit Status, App-Name und Version |
| `GET /api/v1/build-info` | Version und Commit |
| `GET /api/v1/events` | SSE-Stream fuer UI- und Ressourcen-Updates |

Beispiel:

```bash
curl http://127.0.0.1:8000/api/v1/health
```

Im Browser deckt das UI aktuell unter anderem diese Bereiche ab:

- Dashboard
- Server
- Konfigurationen
- Domains
- API Keys
- Benutzer
- Audit-Logs
- Profil

## Projektstruktur

```text
caddybuddy/
├── app/
│   ├── config/            # Settings, Limiter, Logging
│   ├── database/          # Engine, Session-Handling, Initialisierung
│   ├── dependencies/      # Web-Helper, Session- und CSRF-Logik
│   ├── middleware/        # Security Header und Request-Schutz
│   ├── models/            # SQLAlchemy-Modelle
│   ├── repositories/      # Datenzugriff
│   ├── routers/
│   │   ├── api.py         # System-API
│   │   └── ui/            # UI-Routen nach Bereich getrennt
│   ├── services/          # Auth, Build-Info, Caddy, Audit, Events
│   ├── static/            # CSS, JS, Assets
│   ├── templates/         # Jinja2-Templates
│   └── utils/             # Hilfsfunktionen, Banner, Parsing
├── data/                  # Persistente Laufzeitdaten, inkl. SQLite
├── docker/                # Dockerfile und Entry-Setup
├── tools/ui-lint/         # UI-Audit und Browser-basierte Qualitaetschecks
├── VERSION                # Release-Version
├── BUILD_INFO             # Commit oder Build-Metadaten
└── run.py                 # Uvicorn-Einstiegspunkt
```

## Qualitaet und Tooling

### UI-Lint ausfuehren

```bash
cd tools/ui-lint
npm install

UI_LINT_BASE_URL=http://127.0.0.1:8000 \
UI_LINT_USERNAME=admin \
UI_LINT_PASSWORD=admin \
npm run audit
```

Der UI-Lint prueft Layout, Accessibility, Interaktionsflaechen und weitere Frontend-Qualitaetskriterien ueber Playwright und browserseitige Analyzer.

### Python-Syntax schnell pruefen

```bash
python -m py_compile run.py app/main.py
```

## Deployment-Hinweise

- Fuer Produktion sollte CaddyBuddy hinter einem Reverse Proxy mit HTTPS laufen.
- Das Datenverzeichnis `data/` muss persistent gemountet werden.
- `docker/docker-compose.yml` im Repo ist eher ein projektspezifisches Infrastrukturbeispiel und sollte vor produktivem Einsatz an Netzwerk, Secrets und Registry angepasst werden.
- Vor Release-Builds lohnt es sich, `VERSION` und `BUILD_INFO` bewusst zu setzen, damit `/api/v1/build-info` saubere Metadaten liefert.
- Falls du die Anwendung in einer frischen Umgebung startest, wird automatisch ein Default-Admin erzeugt. Dieses Passwort sollte sofort geaendert werden.

---

Wenn du Caddy serverseitig zentral verwalten willst, aber trotzdem eine leichte, nachvollziehbare Python-Webanwendung bevorzugst, ist CaddyBuddy bewusst auf einen kleinen, klaren Stack ausgelegt. 🙂
