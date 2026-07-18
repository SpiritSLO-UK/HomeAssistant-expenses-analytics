# Configuration reference

There are three layers of configuration, in order of precedence at runtime:

1. **Environment variables** (`HAFI_*`) - set at startup. Bootstrap + secrets.
   In the Home Assistant add-on these are produced from the add-on **options**;
   standalone (Docker/compose) you set them directly.
2. **Add-on options** (`config.yaml` schema) - the HA UI form; `run.sh` translates
   them into `HAFI_*` env vars.
3. **In-app Settings** (stored in the database) - runtime-editable knobs surfaced
   on the **Settings** page (base currency, FX mode, AI provider/model, OCR on/off,
   investment price source, log level, retention, …). These override the
   bootstrap defaults once set.

Defaults are chosen so the app runs **private and local with no external calls**.

### Quick start: a `.env` file (standalone)

Running **without** Home Assistant? Instead of editing values one by one, copy the
ready-made template - [`.env.example`](../.env.example) - which lists **every**
`HAFI_*` setting with its default and a comment, all commented out:

```bash
cp .env.example .env              # Windows: copy .env.example .env
# edit .env, then:
docker compose up -d --build      # docker-compose reads ./.env automatically
```

`docker-compose.yml` passes everything in `.env` into the container (it stays
optional - `up` still works with no `.env`). Running from **source** instead of
Docker? The backend reads a `.env` from its working directory, so copy it to
`backend/.env`. `.env` is git-ignored; only the `.env.example` template is
committed. Anything not in the file falls back to the defaults below.

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
| `HAFI_MQTT_USERNAME` / `HAFI_MQTT_PASSWORD` | - | Optional broker credentials. |

### AI gateway (opt-in)

AI is off unless `HAFI_PRIVACY_MODE` selects a real mode **and** a provider is
configured. The API key is a secret and only comes from the environment.

| Variable | Default | Description |
|----------|---------|-------------|
| `HAFI_AI_API_KEY` | - | Secret key for a cloud (or auth'd local) LLM. Never stored in the database. The endpoint + model are non-secret and live in Settings. |
| `HAFI_AI_TIMEOUT_SECONDS` | `30` | Per-request timeout. |

**Use OpenAI / ChatGPT (or any OpenAI-compatible endpoint).** AI is **opt-in** and
only ever *suggests* a category - it never changes one on its own. Cloud payloads
are **minimal and redacted** (description, amount, currency and candidate category
names only; card numbers/IBAN/sort-code/account/postcode/email are stripped, and
receipt OCR text is never sent). Note: this needs an **OpenAI API** key - a ChatGPT
**Plus/UI** subscription can't be used programmatically.

1. **Provide the key.**
   - **Add-on:** Configuration tab → **`ai_api_key`** = your OpenAI key (masked) → Save.
   - **Standalone:** set `HAFI_AI_API_KEY` in the environment / `docker-compose.yml`.
2. **Pick a cloud mode** - add-on `privacy_mode` (or `HAFI_PRIVACY_MODE`) =
   `cloud_manual` (you trigger suggestions) or `cloud_auto`. Restart the add-on.
3. **Point it at OpenAI** in the app - **Settings → AI**: Provider
   `openai_compatible`, Base URL `https://api.openai.com/v1`, Model e.g.
   `gpt-4o-mini` (cheap/fast) or `gpt-4o`. Save.
4. **Try it** - on an uncategorised transaction use the AI suggestion (or the AI
   batch panel). **Settings → Services** shows the AI status; the AI-call log records
   each request (cloud vs local). See [privacy.md](privacy.md) for the redaction
   detail and provider-retention caveats.

> **Local LLM (`local_llm`) - untested, feedback welcome.** The `local_llm` mode
> points the same OpenAI-compatible client at a local endpoint (Ollama, LM Studio,
> a Home Assistant LLM, etc.) so nothing leaves your network. It *should* work with
> any OpenAI-compatible API, but it hasn't been tested against a real local model -
> there isn't one in the author's setup. If you run one, please share what works (and
> what doesn't): models, endpoint quirks, and any requirements. Open an issue on
> GitHub - local-LLM feedback directly shapes this path.

### Investment price feed (opt-in)

Off by default (`manual`). Choose the source in Settings; only the **keyed**
source needs an env key. Only ticker symbols are ever sent. See the main README.

| Variable | Default | Description |
|----------|---------|-------------|
| `HAFI_INVESTMENT_API_KEY` | - | Secret API key for the keyed price provider (e.g. Alpha Vantage). The keyless source (Stooq) and `manual` need nothing. |

### Paperless-ngx import (opt-in)

One-directional: we only ever request documents from Paperless. Off unless both
are set.

| Variable | Default | Description |
|----------|---------|-------------|
| `HAFI_PAPERLESS_URL` | - | Base URL of your Paperless-ngx instance. May instead be set in Settings → Integrations (the env var is the fallback). |
| `HAFI_PAPERLESS_TOKEN` | - | Your Paperless API token (a secret) - **env only**, never stored in the database. |

**Setup walkthrough.** Pulls documents from your own Paperless-ngx into the
Receipts pipeline; it's outbound-only (we only ever *request* from Paperless - it
never receives your finance data) and stays off until both the URL and a token
are present.

1. In Paperless-ngx, create an **API token**: your profile → **My Profile** →
   *API Auth Token* (or **Settings → Administration → Tokens**). Copy it.
2. Give this app the token as a **secret** - set `HAFI_PAPERLESS_TOKEN` in your
   `docker-compose.yml` (or the add-on options) and **restart**. The token is
   never written to the database.
3. Point it at your instance: either set `HAFI_PAPERLESS_URL=http(s)://paperless.local:8000`
   in the same place, **or** enter the URL in **Settings → Integrations ·
   Paperless-ngx** and **Save URL** (the in-app value wins; the env var is the
   fallback). Use **Test connection** to confirm.
4. Once configured, an **Import from Paperless** card appears on the **Receipts**
   page (it's hidden until then). Search your documents and import one as a
   receipt; imports are de-duplicated by content, so re-importing is safe.

### Encryption at rest (optional)

Off by default. `HAFI_DB_KEY` supplies the SQLCipher passphrase that unlocks the
finance database at rest.

| Variable | Default | Description |
|----------|---------|-------------|
| `HAFI_DB_KEY` | - | SQLCipher passphrase for at-rest DB encryption in "stored" unlock mode (so the app can start unattended). In "prompt" mode leave this empty and unlock via the UI each start. On the **Home Assistant add-on**, set the `db_key` option in the **Configuration** tab instead (it maps to this var) - see the add-on options table below. See [security.md](security.md). |

> **⚠️ By default the database is stored UNENCRYPTED (plaintext SQLite) on the
> volume.** At-rest encryption is **opt-in**, and neither the standalone
> (Docker/compose) path nor the add-on sets `HAFI_DB_KEY` for you - so unless you
> turn encryption on, anyone who can read the raw DB file (a stolen disk, an
> untrusted backup destination, host/root access) can read your data. To enable it:
>
> 1. Turn on encryption **in the app** - **Settings → Database encryption** -
>    choosing a passphrase. This encrypts the existing DB in place.
> 2. Decide how it unlocks after a restart:
>    - **"Stored" (unattended):** set `HAFI_DB_KEY` (standalone/compose env var) or
>      the `db_key` add-on option to that same passphrase, and the app unlocks
>      itself on every start. The key then lives on the device - weaker, but no
>      manual step.
>    - **"Prompt":** leave `HAFI_DB_KEY` empty and re-enter the passphrase via the
>      UI after each restart; it's never written to disk.
>
> The SQLCipher driver ships in the Linux add-on image (amd64 + aarch64/Raspberry
> Pi) but has **no Windows wheel**, so encryption is unavailable on Windows dev.
> A lost passphrase is **unrecoverable**. See [security.md](security.md) for the
> threat model and what encryption does / doesn't protect against.

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
| `mqtt_username` / `mqtt_password` | - | Optional. |
| `log_level` | `INFO` | Logging verbosity. |
| `db_key` | - | At-rest encryption passphrase for **"stored"** unlock mode. Set it to the same passphrase you encrypted with (Settings → Database encryption) and the add-on unlocks itself on every restart. Leave blank for **"prompt"** mode (re-enter via the UI after each restart; the key is never written to disk). Stored here it lives on the device - weaker, opt-in. |

The cloud-AI key **is** in the add-on schema (`ai_api_key`). Secrets for the other
opt-in features (investment price feed / Paperless) aren't in the add-on schema
yet - set them as env vars if you run via Docker, or they'll be added to the
add-on options as those integrations graduate.

---

## In-app Settings (database-stored)

Editable on the **Settings** page (some are owner- or settings-manager-only):

- **Base currency** - curated top-10 dropdown; recomputes display conversions.
- **Services** - AI on/off + status, OCR on/off, online FX (manual ↔ Frankfurter),
  MQTT (read-only status).
- **AI** - provider (`none` / OpenAI-compatible), base URL, model.
- **Investment price source** - `manual` / `stooq` / `alphavantage`.
- **Logging** - runtime log level.
- **Data retention** - per-type archive/purge windows, auto-purge, backup-trim
  limits (owner + MFA-gated).
- **MFA** - enrol/disable your own two-factor (personal).

See [troubleshooting.md](troubleshooting.md) if a setting doesn't behave as
expected, and [configuration precedence](#configuration-reference) above for which
layer wins.

---

## Logging - what each level records

Set the level with `HAFI_LOG_LEVEL` (bootstrap) or **Settings → Logging**
(runtime; takes effect immediately). Each level **includes everything above it**,
so `DEBUG` shows the most and `ERROR` the least. Logs stream to the container
output (`docker compose logs -f`) or the add-on **Log** tab.

| Level | What you'll see | When to use |
|-------|-----------------|-------------|
| `ERROR` | Only serious failures - an unhandled exception, or a critical misconfiguration (e.g. at-rest encryption is enabled but the SQLCipher driver is missing, so the DB locks). | Quietest; production once you trust it. |
| `WARNING` | The above **plus** recoverable problems where an optional step failed and was skipped - an FX or price-feed lookup that errored, OCR/subscription-detection falling back, a non-fatal MQTT publish failure. | A sensible quiet-but-watchful setting. |
| `INFO` *(default)* | The above **plus** normal lifecycle milestones - startup/shutdown, a statement imported (counts), the retention sweep summary. No transaction contents. | The recommended default. |
| `DEBUG` | The above **plus** verbose per-step detail. Useful when chasing a bug. | Temporary; turn it back down afterwards. |

Notes:
- The runtime log is **operational**, not an audit trail. Sensitive actions
  (imports, deletes, role/approval changes, AI calls) are recorded separately in
  the owner-only **Logs** page (activity log + AI-call log), independent of this
  level.
- Logs avoid transaction descriptions and amounts even at `DEBUG`; anything sent
  to a cloud AI is minimised and redacted first (see [privacy.md](privacy.md)).
