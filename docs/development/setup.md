# Local Development

## Environment

CaddyBuddy requires Python 3.13.

```bash
git clone https://github.com/Gill-Bates/caddybuddy.git
cd caddybuddy

python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Start the application:

```bash
export CB_SECRET_KEY="$(head -c 32 /dev/urandom | base64)"
export LOG_LEVEL=DEBUG
python run.py
```

## Tests

Run the Python test suite:

```bash
python -m pytest
```

UI lint tooling is maintained under `tools/ui-lint` and uses Playwright:

```bash
cd tools/ui-lint
npm install
npx playwright install chromium firefox webkit
npm run audit
```

The browser audit expects a running CaddyBuddy instance and credentials provided through its documented environment variables.

## Documentation

```bash
python -m pip install -r docs/requirements-docs.txt
cp CHANGELOG.md docs/changelog.md
mkdocs serve -f docs/mkdocs.yml
```

Open `http://127.0.0.1:8000` for the documentation preview unless another port is selected.
