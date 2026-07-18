# Standalone (docker-compose)

Run **HA Finance Intelligence** on its own with Docker - the same complete,
privacy-first finance app, **without Home Assistant**. With no HA identity in
front, it runs **single-user** as a local owner. Current version: **v1.0.2**.

> Prefer to run it **inside Home Assistant** as an ingress sidebar panel (SSO,
> MQTT sensors, energy-cost offset)? Use the **add-on** package instead - see
> [ha-install.md](ha-install.md).

## Requirements

- **Docker** with the **Compose v2** plugin (`docker compose …`). Compose v2.24+
  is recommended so the optional `.env` file is picked up automatically; on older
  Compose, set the vars in the `environment:` block of `docker-compose.yml`
  instead.
- A few hundred MB of RAM and a little disk (your statements + the SQLite DB +
  optional safety backups; receipt images are the main consumer if you keep them).
  Runs on x86 or ARM, including a Raspberry Pi.

## Quick start

```bash
git clone https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics.git
cd HomeAssistant-expenses-analytics
docker compose up -d --build      # build + start
# open http://localhost:8099
```

Then load some data to explore: **Settings → Demo data → Load demo data** (or
`curl -X POST http://localhost:8099/api/backup/demo`), and **Settings → Remove
demo data** when you're done.

Everyday commands:

```bash
docker compose logs -f            # follow logs
docker compose down               # stop (your data is kept in the volume)
docker compose up -d              # start again
```

## Your data

All data (the SQLite database + uploads + safety backups) lives in the named
Docker volume **`finance_data`**, mounted at `/data` in the container. The compose
file forces `HAFI_DATABASE_PATH=/data/finance.db` regardless of `.env`, so the DB
always lands on that persistent volume. `docker compose down` keeps the volume;
back it up (or use the in-app **encrypted backups**) before anything destructive.

## Configuration (env vars)

Copy the template and edit the values you care about - every key is **optional**
and falls back to a safe, fully-local default (AI off, MQTT off, no external
calls):

```bash
cp .env.example .env              # Windows: copy .env.example .env
docker compose up -d              # Compose reads ./.env automatically
```

`.env` is git-ignored, so secrets stay local. Anything in the compose
`environment:` block overrides the `.env` file. Common variables:

| Variable | Default | What it does |
|----------|---------|--------------|
| `HAFI_CURRENCY` | `GBP` | Base (display) currency; amounts are stored in their original currency. Changeable later in Settings. |
| `HAFI_PRIVACY_MODE` | `strict_local` | AI posture: `strict_local` / `no_ai` = off; `local_llm` = local endpoint; `cloud_manual` = cloud, approve each request; `cloud_auto` = cloud, automatic. |
| `HAFI_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. Also editable at runtime in Settings. |
| `HAFI_TRUST_PROXY_HEADERS` | `true` | Whether to trust `X-Remote-User-*` identity headers. See the trust-model caveat below. |
| `HAFI_MQTT_ENABLED` | `false` | Publish finance sensors over MQTT (with `HAFI_MQTT_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD`). |
| `HAFI_OCR_ENABLED` | `false` | Local receipt/scanned-statement OCR (Tesseract; bundled in the image). |
| `HAFI_AI_API_KEY` | _(empty)_ | Secret key for a cloud (or auth'd local) LLM; the endpoint + model are chosen in **Settings → AI**. |
| `HAFI_PAPERLESS_URL` / `HAFI_PAPERLESS_TOKEN` | _(empty)_ | Opt-in, one-directional import from your own Paperless-ngx. |
| `HAFI_DB_KEY` | _(empty)_ | SQLCipher passphrase for **"stored"** at-rest encryption (unattended unlock); leave empty for **"prompt"** mode (unlock in the UI each start). |

Most runtime knobs (AI provider/endpoint/model, OCR, online FX rates, retention,
the base currency after first run) are edited **in-app** on the **Settings** page
and stored in the database - not here. The full list is in the
[configuration reference](configuration.md).

## Exposure & trust model - read before going beyond localhost

The shipped `docker-compose.yml` publishes the app on **`8099:8099`**, which binds
the port to **every** host interface with **no reverse proxy in front**. That is
fine on a single machine you reach over `localhost`, but it is **not safe to expose
to a wider network as-is**: the app derives identity from `X-Remote-User-*` headers
and bootstraps the first caller it sees as the **owner**, so anyone who can reach
the raw port could forge those headers and take over.

Before exposing it beyond the host, do **one** of:

1. **Keep it localhost-only** - bind the published port to loopback:
   `ports: ["127.0.0.1:8099:8099"]`.
2. **Front it with an authenticating, header-stripping reverse proxy** that
   terminates TLS and injects its own trusted identity - see
   [reverse-proxy.md](reverse-proxy.md) and the bundled `docker-compose.tls.yml` /
   `Caddyfile`.
3. **Turn off header trust** - set `HAFI_TRUST_PROXY_HEADERS=false` so the app
   ignores all `X-Remote-User-*` headers and resolves every request to the single
   `local` owner.

Full detail and the reasoning are in **[security.md](security.md)**. To serve it
over HTTPS across your network, see **[reverse-proxy.md](reverse-proxy.md)**
(bundled Caddy: `SITE_ADDRESS=finance.example.com docker compose -f
docker-compose.tls.yml up -d --build`).

## Upgrading

```bash
git pull
docker compose up -d --build      # rebuild the image and restart
```

The `finance_data` volume carries over and **database migrations run automatically
on start**, so your data and config survive the upgrade. See the
[CHANGELOG](../CHANGELOG.md) for what each release changes. Back up first if you
like - the in-app **encrypted backups** (Settings) give you an off-device copy.

> Provided **"as is", without warranty**, and **not** financial advice. You are
> responsible for your own backups and for verifying figures before relying on
> them. See [privacy.md](privacy.md) and [security.md](security.md).
