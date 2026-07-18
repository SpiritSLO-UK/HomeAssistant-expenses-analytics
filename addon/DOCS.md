# HA Finance Intelligence

A **local-first, Home Assistant-first personal finance app**. Import your bank
statements, categorise transactions, track budgets, projects, subscriptions and
savings, scan receipts, handle multiple currencies — and publish finance sensors
to Home Assistant. **Privacy-first: strict local mode is the default, and nothing
leaves your machine unless you explicitly opt in.**

It runs as an ingress panel (no extra login — your Home Assistant identity signs
you in), stores everything in the add-on's **private `/data` volume** (included in
Home Assistant backups), and pulls a prebuilt image so install is a quick download.

> This is the **add-on** package (current version **v1.0.2**). Prefer to run it
> **without** Home Assistant, on Docker? See the standalone guide,
> [docs/standalone.md](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/blob/main/docs/standalone.md).

## What it does

- **Import & categorise** — CSV/OFX/QIF and PDF statements; a rules engine plus a
  vendor/category library auto-categorise; split a transaction across categories.
- **Budgets, projects, subscriptions, savings, investments, cars & home assets.**
- **Receipts** — local OCR (Tesseract) extracts and matches receipts to transactions.
- **Multi-currency** — amounts kept in their original currency; one base currency
  for display, with manual or online FX rates.
- **Multi-user household** — owner/member/viewer/child roles, shared vs private
  accounts, per-member views; the first person to open the add-on becomes the owner.
- **Home Assistant sensors (MQTT)** — spend/income/net, review count, per-budget
  progress, per-project totals and more, via MQTT discovery (off by default). The
  broker is auto-discovered from the Supervisor — no credentials to type.
- **Energy-cost offset** — net your HA solar/grid production against your energy-bill
  spend to see a live cost offset (optional; reads only the entities you name).
- **Optional AI** — suggests categories using a local LLM or an OpenAI-compatible
  endpoint. **Off by default**; cloud payloads are minimal and redacted. AI only
  ever *suggests* — it never changes a category on its own.

## Setup

1. Install the add-on, then on the **Configuration** tab set your **currency**
   (and optionally **MQTT** / **AI** options). The **energy-cost offset** is
   configured in-app (Settings → Energy), not on the Configuration tab.
2. **Start** the add-on, then **Open Web UI** (enable **"Show in sidebar"** on the
   **Info** tab to pin a **Finance** entry to Home Assistant's left menu).
3. Import a statement (or load demo data from **Settings**) and you're away.

Full walkthrough: **[Install guide](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/blob/main/docs/ha-install.md)**
· **[Configuration reference](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/blob/main/docs/configuration.md)**
· **[Troubleshooting](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/blob/main/docs/troubleshooting.md)**.

## Add-on options

Set these on the add-on's **Configuration** tab. They are the only bootstrap
settings — most runtime knobs (AI provider/endpoint/model, OCR, online FX rates,
retention, the base currency after first run) are edited **in-app** on the
**Settings** page and stored in the database.

| Option | Default | What it does |
|--------|---------|--------------|
| `database_path` | `/data/finance/finance.db` | SQLite database file inside the add-on's private, backed-up `/data` volume. Leave as-is unless you have a reason to move it. |
| `currency` | `GBP` | Base (display) currency totals are converted to; amounts are always kept in their original currency. Changeable later in Settings. |
| `privacy_mode` | `strict_local` | AI posture. `strict_local` / `no_ai` = AI fully off (no external calls); `local_llm` = a local OpenAI-compatible endpoint; `cloud_manual` = a cloud LLM you approve per request; `cloud_auto` = a cloud LLM called automatically. |
| `ai_api_key` | _(empty)_ | Masked secret key for a cloud (or auth'd local) LLM — the only AI secret. The endpoint + model are non-secret and chosen in **Settings → AI**. Leave blank unless you use a cloud AI mode. |
| `mqtt_enabled` | `false` | Publish finance sensors (spend/income/net, review count, per-budget/per-project totals, monthly subscriptions) to Home Assistant via MQTT discovery. |
| `mqtt_host` | `core-mosquitto` | Broker host. When left default with no username set, the broker is auto-discovered from the Supervisor (the Mosquitto add-on) — no credentials to type. |
| `mqtt_port` | `1883` | Broker port (1024–65535). |
| `mqtt_username` | _(empty)_ | Optional broker username. |
| `mqtt_password` | _(empty)_ | Optional broker password (masked). |
| `log_level` | `INFO` | Verbosity of the add-on **Log** panel: `DEBUG` / `INFO` / `WARNING` / `ERROR`. |
| `db_key` | _(empty)_ | At-rest DB encryption passphrase for **"stored"** unlock mode (Settings → Database encryption) — set it to the passphrase you encrypted with and the add-on unlocks itself on every restart. Leave blank for **"prompt"** mode (more secure: the key is never written to disk, but you re-enter it after each restart). Stored here it lives on the device — a weaker posture, so opt-in. See [security.md](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/blob/main/docs/security.md). |

The **energy-cost offset** (net your HA solar/grid production against your
energy-bill spend) is configured **in-app** at **Settings → Energy**, not here.

## Ingress & usage

The add-on is **ingress-only** — it maps no host port (`host_network: false`) and
is reached through Home Assistant, which authenticates you and injects your
identity. **Open Web UI** from the add-on's Info tab, or enable **"Show in
sidebar"** to pin a **Finance** panel to Home Assistant's left menu. The first
person to open it becomes the **owner**; anyone new appears **pending** until the
owner approves them.

## Updating

New versions ship as prebuilt images. When one is available, the add-on shows an
**Update** button (**Settings → Add-ons → HA Finance Intelligence**) — click it and
the Supervisor pulls the new image. Your data in `/data` is untouched and
**database migrations run automatically on start**, so config and data carry over.
Back up first if you like — `/data` is included in Home Assistant backups. See the
[changelog](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/blob/main/addon/CHANGELOG.md)
for what each release changes.

## Privacy & security

Local-first by design. Strict-local mode (the default) makes **no external calls**.
Data lives in the add-on's private `/data` volume — no shared folders, so other
add-ons and the Home Assistant `/config` share can't read it. See
[Privacy](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/blob/main/docs/privacy.md)
and [Security](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/blob/main/docs/security.md).

> **Not financial advice**, provided "as is" without warranty. Keep your own
> backups and verify figures before relying on them.
