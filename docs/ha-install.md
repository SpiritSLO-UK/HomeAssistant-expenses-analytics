# Install on Home Assistant

HA Finance Intelligence runs on Home Assistant as an **add-on**: a single container
served through Home Assistant **ingress** (a sidebar panel), signed in with your
Home Assistant identity, with finance sensors published over **MQTT**. It installs
from a **prebuilt multi-arch image** on GHCR, so Home Assistant *pulls* the image
instead of building it on your device — install is quick, even on a Raspberry Pi.

> **Requires Home Assistant with the Supervisor** — i.e. **Home Assistant OS** or a
> **Supervised** install. Home Assistant *Container* and *Core* installs don't have
> the add-on store; run the app standalone with Docker instead (see the
> [main README](../README.md) and [HTTPS / reverse proxy](reverse-proxy.md)).

## 1. Add the repository

Click the badge (in the [main README](../README.md#-install-on-home-assistant-add-on)):

> **Add repository to your Home Assistant** → it opens your HA and pre-fills the
> repository URL; confirm to add it.

…or add it manually: open the Add-on Store (**Settings → Add-ons → Add-on Store**),
then **⋮ (top-right) → Repositories**, paste the URL below and click **Add**:

```
https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics
```

and click **Add**, then **Close**.

## 2. Install

Refresh the store; **HA Finance Intelligence** appears (you may need to scroll to
the bottom, or search). Open it and click **Install**. Supervisor pulls the image
for your architecture (`aarch64` for a Pi, `amd64` for Intel/AMD) — typically about
a minute, no compiling.

## 3. Configure

On the add-on's **Configuration** tab:

| Option | What it does | Default |
|--------|--------------|---------|
| `currency` | Your base currency (e.g. `GBP`, `USD`, `EUR`). | `GBP` |
| `privacy_mode` | AI mode. `strict_local` = AI off, nothing leaves your machine. Others opt into local/cloud AI. | `strict_local` |
| `mqtt_enabled` | Publish finance sensors to Home Assistant over MQTT discovery. | `false` |
| `mqtt_host` / `mqtt_port` / `mqtt_username` / `mqtt_password` | **Optional overrides** — by default the add-on auto-discovers the Supervisor's MQTT broker (e.g. the Mosquitto add-on), so you usually leave these blank. Set them only to point at a different broker. | _(auto)_ |
| `log_level` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. | `INFO` |
| `database_path` | Where the SQLite DB lives (inside the add-on's private `/data`). | `/data/finance/finance.db` |

Click **Save**, then **Start** (and turn on **Start on boot** /
**Watchdog** if you like). The full settings reference is in
[configuration.md](configuration.md).

## 4. Open it

Click **Open Web UI**. To also get a **Finance** entry in Home Assistant's
**left sidebar**, turn on **"Show in sidebar"** on the add-on's **Info** tab — then
**restart the add-on and refresh** the Home Assistant page for it to appear. The
app loads through ingress, so:

- **Single sign-on** — you're signed in as your Home Assistant user automatically;
  no separate login. The **first** person to open the add-on becomes the **owner**
  (full admin). Anyone who opens it afterwards starts as a **pending member** with
  no data access until the owner approves them under **Users**.
- Everything is served under the ingress path; no port to expose.

## 5. MQTT sensors (optional)

With `mqtt_enabled` on and a broker configured, the add-on publishes a **HA Finance
Intelligence** device via MQTT discovery. Find it under **Settings → Devices &
Services → MQTT**. Sensors include spend / income / net this month, review-queue
count, uncategorised count, monthly subscriptions total, and one per budget /
project. Use them in dashboards and automations like any other sensor. Publishing
is off by default and best-effort — a broker problem never blocks the app.

## 6. Energy-cost offset (optional)

If you have solar/grid/energy sensors in Home Assistant, the app can net your
production against your energy-bill spend to show a live cost offset. Configure it
in the app under **Settings → Energy** — pick a data source (`Home Assistant API`
to read named HA entities, or `MQTT` to read topics), set your tariff (or let it
derive the unit price from your Home utility-meter readings), and choose which spend
category is your energy bill. Reading HA entities requires the add-on's
`homeassistant_api` permission (shown at install) and is **read-only**, used only
for the entities you name and only when this feature is turned on.

## Storage, backups and isolation

Your data lives in the add-on's **private `/data` volume** — no shared folders, so
no other add-on and not Home Assistant's `/config` can read it, and this add-on
can't read your wider Home Assistant config. `/data` is included in **Home
Assistant backups** (Settings → System → Backups), so a full HA backup captures
your finance database, uploads and safety backups. You can also export from inside
the app (database snapshot, CSV, category/rule/vendor libraries). See
[security.md](security.md) and [privacy.md](privacy.md).

## Updating

When a new version is published, the add-on shows an **Update** button (Supervisor
pulls the new image). Your data in `/data` is preserved across updates; database
migrations run automatically on start.

## Uninstalling

Uninstalling the add-on removes the container. Your data in `/data` is removed with
the add-on — **export or back up first** if you want to keep it.

## Trouble?

See [troubleshooting.md](troubleshooting.md) for ingress, MQTT, AI, import and OCR
fixes. The add-on's **Log** tab (raise `log_level` to `DEBUG`) is the first place
to look.
