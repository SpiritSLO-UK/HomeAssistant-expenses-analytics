# Testing on your local Home Assistant (before a release)

This is the end-to-end checklist for validating the add-on on a real Home Assistant
(e.g. a Raspberry Pi) before tagging a release. It captures the gotchas we actually
hit, so each one is a quick fix rather than a head-scratch.

There are two layers: a **standalone smoke test** (fast, no HA) and the **full HA
add-on test** (the real thing).

---

## Layer 0 — standalone smoke test (no HA)

Quickest sanity check that the image boots and serves the app:

```bash
docker compose up -d --build       # build + start
# open http://localhost:8099       → the app loads
docker compose logs -f             # watch the logs
docker compose down                # stop (data kept in the volume)
```

Confirm: the page loads, **Settings → Demo data → Load demo data** populates it, and
a statement imports. (CI also boots the image and smoke-tests `/api/health` + the
frontend on every PR, so this rarely surprises you.)

---

## Layer 1 — full HA add-on test

The add-on installs from a **prebuilt multi-arch image on GHCR**, so your Pi *pulls*
the image (no on-device build). That means two things must be true before you can
install: the image has to be **published**, and its GHCR package must be **public**.

### 1. Publish a release-candidate image

Tag an RC — the release workflow builds + pushes `amd64` and `aarch64` images.
Replace `N` with the RC number and the version with the one you're cutting (e.g.
`v1.0.2-rc1`):

```bash
git tag -a v1.0.0-rcN -m "v1.0.0-rcN"
git push origin v1.0.0-rcN
```

Watch it: `gh run watch <id> --exit-status`. Keep `addon/config.yaml`'s `version:`
equal to the tag (minus the leading `v`) — Supervisor pulls `image:version`, so they
**must** match.

> **First time only — make the GHCR packages public.** New GHCR packages are
> **private**, so Supervisor can't pull them and the install fails with
> `[403] … manifests/…: denied`. Set **both** packages public, once:
> `https://github.com/users/SpiritSLO-UK/packages/container/{aarch64,amd64}-ha-finance-intelligence/settings`
> → **Danger Zone → Change visibility → Public**. (New tags inherit the public
> setting, so you only do this once per package.)
>
> Verify a package is anonymously pullable (what Supervisor does):
> ```bash
> tok=$(curl -s "https://ghcr.io/token?service=ghcr.io&scope=repository:spiritslo-uk/aarch64-ha-finance-intelligence:pull" | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
> curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $tok" \
>   https://ghcr.io/v2/spiritslo-uk/aarch64-ha-finance-intelligence/manifests/1.0.0-rcN   # → 200
> ```

### 2. Add the repository & install

1. **Settings → Add-ons → Add-on store → ⋮ (top-right) → Repositories** → paste
   `https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics` (or click the
   **"Add to my Home Assistant"** badge in the README) → **Add**.
2. Open **HA Finance Intelligence** in the store → **Install** (Supervisor pulls the
   `aarch64` image — about a minute, no compiling).
3. **Configuration** tab → set `currency`; optionally `mqtt_enabled: true`, an AI
   mode, or the energy options → **Save** → **Start**.

### 3. Open it + check ingress SSO

- Click **Open Web UI**. You should land in the app with **no login** — you're signed
  in as your Home Assistant user (ingress SSO), and the **first** user to open it is
  the **owner**. Confirm under **Settings → Users** that you show as **owner /
  approved**.
- **Sidebar panel:** to pin a **Finance** entry to HA's left menu, enable
  **"Show in sidebar"** on the add-on's **Info** tab, then **restart the add-on and
  refresh** the HA page. (A fresh ingress add-on doesn't show there until you do this.)

### 4. MQTT sensors (optional)

With `mqtt_enabled: true`, the add-on **auto-discovers the broker from the
Supervisor** — you don't enter host/credentials (it connects as the Supervisor's
`addons` user). Then:

- **Settings → Devices & Services → MQTT** → an **HA Finance Intelligence** device
  with `Finance *` sensors (spend/income/net, review count, per-budget, per-project,
  and the energy offset when configured).
- In the app, **Settings → Home Assistant sensors (MQTT)** shows status + a
  **Publish now** button. The Mosquitto add-on log should show
  `New client connected … (… u'addons')` — that's a successful connect.
- If the broker log shows `received null username or password … not authorised`, the
  add-on is connecting with no credentials — make sure you're on a build with MQTT
  auto-discovery (v1.0.0-rc2+), or set `mqtt_username`/`mqtt_password` manually.

### 5. Energy-cost offset (optional)

If you have HA energy sensors, open the **Energy** page → **Settings**:

- Source **Home Assistant API** → add a production entity (ideally a "this-month"
  kWh **Utility Meter** sensor) — this needs the add-on's `homeassistant_api` access
  (granted at install). Or source **MQTT** → list the topics.
- Set your **energy-bill category** and a **tariff** (or leave it blank to derive the
  price from your Home electricity meter readings).
- The page shows produced kWh → saving → energy spend → net cost. Flip the source to
  verify both paths.

### 6. Storage, backups & multi-user

- Data lives in the add-on's private `/data`. Take a **Home Assistant backup**
  (Settings → System → Backups) and confirm the add-on is captured.
- Open the add-on as a **second HA user** → they appear as **pending** under Users
  until the owner approves them.
- **Update in place:** publish a higher RC, click the add-on's **Update** button, and
  confirm your data survives (migrations run automatically on start).

---

## Release checklist

All green → bump the version everywhere, update the CHANGELOG, and tag `v1.0.0`.

- [ ] Standalone `docker compose up` boots; app loads; import works
- [ ] Add-on installs from the public image (no `403`)
- [ ] Ingress loads; **SSO** signs you in as **owner**
- [ ] **Show in sidebar** pins the Finance panel
- [ ] Core flows: import → categorise → budgets → receipts
- [ ] **MQTT** sensors discovered (broker auto-discovered, no manual creds)
- [ ] **Energy offset** works for `ha_api` and `mqtt`
- [ ] Backup/restore captures `/data`; **update-in-place** keeps data
- [ ] Second HA user → pending → approve flow
- [ ] Image stays lean (no `examples/`/tests inside)

## Quick troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Install fails `[403] … denied` | GHCR package is **private** → make both packages public (step 1). |
| Add-on not in the sidebar | Enable **"Show in sidebar"** (Info tab) + restart + refresh. |
| MQTT `not authorised` / no sensors | Broker needs auth → MQTT auto-discovery (rc2+) handles it; else set `mqtt_username`/`mqtt_password`. Click **Publish now**. |
| Energy offset shows 0 saving | No unit price → set a tariff or log Home electricity meter readings with costs. |
| Encryption "unavailable" | At-rest SQLCipher is available on both amd64 and aarch64 (the arm64 wheel is compiled into the image); if a custom/minimal image omits it, the private `/data` isolation still applies. |

See [troubleshooting.md](troubleshooting.md) for the wider list.
