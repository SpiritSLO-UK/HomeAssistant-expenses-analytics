# Configuration reference

There are three layers of configuration, in order of precedence at runtime:

1. **Environment variables** (`HAFI_*`) — set at startup. Bootstrap + secrets.
   In the Home Assistant add-on these are produced from the add-on **options**;
   standalone (Docker/compose) you set them directly.
2. **Add-on options** (`config.yaml` schema) — the HA UI form; `run.sh` translates
   them into `HAFI_*` env vars.
3. **In-app Settings** (stored in the database) — runtime-editable knobs surfaced
   on the **Settings** page (base currency, FX mode, AI provider/model, OCR on/off,
   investment price source, log level, retention, …). These override the
   bootstrap defaults once set.

Defaults are chosen so the app runs **private and local with no external calls**.

---

## Environment variables (`HAFI_*`)

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `HAFI_DATABASE_PATH` | `./data/finance.db` | SQLite database file path. In the add-on/compose this lives on the persistent `/data` volume. |
| `HAFI_CURRENCY` | `GBP` | Base (display) currency. Amounts are stored in their original currency; this is what totals are converted to. Changeable later in Settings. |
| `HAFI_PRIVACY_MODE` | `strict_local` | AI posture: `strict_local` / `no_ai` (both = AI off), `local_llm`, `cloud_manual`, `cloud_auto`. See [privacy.md](privacy.md). |
| `HAFI_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. Also editable in Settings. |
| `HAFI_PORT` | `8099` | HTTP port. |
| `HAFI_HOST` | `0.0.0.0` | Bind address. |

### MQTT (publish sensors to Home Assistant)

Off by default. Only used when `HAFI_MQTT_ENABLED=true`.

| Variable | Default | Description |
|----------|---------|-------------|
| `HAFI_MQTT_ENABLED` | `false` | Publish finance sensors via MQTT discovery. |
| `HAFI_MQTT_HOST` | `core-mosquitto` | Broker host (the HA Mosquitto add-on). |
| `HAFI_MQTT_PORT` | `1883` | Broker port. |
| `HAFI_MQTT_USERNAME` / `HAFI_MQTT_PASSWORD` | — | Optional broker credentials. |

### AI gateway (opt-in)

AI is off unless `HAFI_PRIVACY_MODE` selects a real mode **and** a provider is
configured. The API key is a secret and only comes from the environment.

| Variable | Default | Description |
|----------|---------|-------------|
| `HAFI_AI_API_KEY` | — | Secret key for a cloud (or auth'd local) LLM. Never stored in the database. The endpoint + model are non-secret and live in Settings. |
| `HAFI_AI_TIMEOUT_SECONDS` | `30` | Per-request timeout. |

### Investment price feed (opt-in)

Off by default (`manual`). Choose the source in Settings; only the **keyed**
source needs an env key. Only ticker symbols are ever sent. See the main README.

| Variable | Default | Description |
|----------|---------|-------------|
| `HAFI_INVESTMENT_API_KEY` | — | Secret API key for the keyed price provider (e.g. Alpha Vantage). The keyless source (Stooq) and `manual` need nothing. |

### Paperless-ngx import (opt-in)

One-directional: we only ever request documents from Paperless. Off unless both
are set.

| Variable | Default | Description |
|----------|---------|-------------|
| `HAFI_PAPERLESS_URL` | — | Base URL of your Paperless-ngx instance. |
| `HAFI_PAPERLESS_TOKEN` | — | Your Paperless API token (a secret). |

### Encryption at rest (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `HAFI_DB_KEY` | — | SQLCipher passphrase for at-rest DB encryption in "stored" unlock mode (so the add-on can start unattended). In "prompt" mode leave this empty and unlock via the UI each start. See [security.md](security.md). |

---

## Add-on options (Home Assistant)

Set these in the add-on's **Configuration** tab; `run.sh` maps them to the
`HAFI_*` vars above. (`config.yaml` schema.)

| Option | Default | Notes |
|--------|---------|-------|
| `database_path` | `/data/finance/finance.db` | On the add-on's private `/data` volume. |
| `currency` | `GBP` | Base currency. |
| `privacy_mode` | `strict_local` | One of the five privacy modes. |
| `mqtt_enabled` | `false` | Publish sensors. |
| `mqtt_host` / `mqtt_port` | `core-mosquitto` / `1883` | Broker. |
| `mqtt_username` / `mqtt_password` | — | Optional. |
| `log_level` | `INFO` | Logging verbosity. |

Secrets used only by standalone/opt-in features (AI / investment / Paperless
keys) are not in the add-on schema yet — set them as env vars if you run via
Docker, or they'll be added to the add-on options as those integrations graduate.

---

## In-app Settings (database-stored)

Editable on the **Settings** page (some are owner- or settings-manager-only):

- **Base currency** — curated top-10 dropdown; recomputes display conversions.
- **Services** — AI on/off + status, OCR on/off, online FX (manual ↔ Frankfurter),
  MQTT (read-only status).
- **AI** — provider (`none` / OpenAI-compatible), base URL, model.
- **Investment price source** — `manual` / `stooq` / `alphavantage`.
- **Logging** — runtime log level.
- **Data retention** — per-type archive/purge windows, auto-purge, backup-trim
  limits (owner + MFA-gated).
- **MFA** — enrol/disable your own two-factor (personal).

See [troubleshooting.md](troubleshooting.md) if a setting doesn't behave as
expected, and [configuration precedence](#configuration-reference) above for which
layer wins.
