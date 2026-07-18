# HA Finance Intelligence — Home Assistant add-on

A **local-first personal finance app**, packaged as a Home Assistant add-on:
import bank statements, categorise spending, track budgets/projects/subscriptions/
savings, scan receipts (local OCR), handle multiple currencies, publish finance
sensors over MQTT, and net an energy-cost offset — with **optional, opt-in** AI and
**strict local mode as the default**. Current version: **v1.0.2**.

It installs from a **prebuilt multi-arch image** (amd64 + aarch64/Raspberry Pi — no
on-device build), runs as an **ingress sidebar panel** (your Home Assistant identity
signs you in), and keeps all data in the add-on's **private `/data` volume**
(included in Home Assistant backups).

## Install

[![Add repository to your Home Assistant.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FSpiritSLO-UK%2FHomeAssistant-expenses-analytics)

1. Click the badge above (it opens Home Assistant and pre-fills the repository
   URL), or add it manually: **Settings → Add-ons → Add-on Store → ⋮ →
   Repositories**, paste
   `https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics`, **Add**.
2. Open **HA Finance Intelligence** in the store and click **Install**.
3. On the **Configuration** tab set your **currency** (and optionally MQTT / AI /
   `db_key`), then **Start** and **Open Web UI**.

_Requires a Home Assistant install with the Supervisor (Home Assistant OS or
Supervised)._

## Configure, use & upgrade

The Configuration-tab options, ingress usage and upgrade steps are documented in
**[DOCS.md](DOCS.md)** (also shown on the add-on's **Documentation** tab in Home
Assistant). Deeper guides live in the repo's [`docs/`](../docs/README.md):
[Install walkthrough](../docs/ha-install.md) ·
[Configuration reference](../docs/configuration.md) ·
[Privacy](../docs/privacy.md) · [Security & isolation](../docs/security.md) ·
[Troubleshooting](../docs/troubleshooting.md). What's new per release is in the
[changelog](CHANGELOG.md).

> **Running without Home Assistant?** Use the **standalone (docker-compose)**
> package instead — see [docs/standalone.md](../docs/standalone.md).

> **Not financial advice**, provided "as is" without warranty. Keep your own
> backups and verify figures before relying on them.
