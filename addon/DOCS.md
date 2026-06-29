# HA Finance Intelligence

A **local-first, Home Assistant-first personal finance app**. Import your bank
statements, categorise transactions, track budgets, projects, subscriptions and
savings, scan receipts, handle multiple currencies — and publish finance sensors
to Home Assistant. **Privacy-first: strict local mode is the default, and nothing
leaves your machine unless you explicitly opt in.**

It runs as an ingress panel (no extra login — your Home Assistant identity signs
you in), stores everything in the add-on's **private `/data` volume** (included in
Home Assistant backups), and pulls a prebuilt image so install is a quick download.

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

## Privacy & security

Local-first by design. Strict-local mode (the default) makes **no external calls**.
Data lives in the add-on's private `/data` volume — no shared folders, so other
add-ons and the Home Assistant `/config` share can't read it. See
[Privacy](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/blob/main/docs/privacy.md)
and [Security](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/blob/main/docs/security.md).

> **Not financial advice**, provided "as is" without warranty. Keep your own
> backups and verify figures before relying on them.
