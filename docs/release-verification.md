# Release verification checklist (v1.1.0)

Two parts: **Part A** you can run right now on the localhost docker image, no Pi
needed. **Part B** needs the add-on installed on the Pi. Tick each `- [ ]` and
write what you saw on the `Result:` line. When the "blocks release" items pass,
tell Claude "checklist done, cut v1.1.0".

- Candidate: `main` (release prep done; last release was v1.0.2, next is v1.1.0)
- Tester: ____________   Date: ____________

---

# Part A - Local (localhost docker image)

Run against the standalone docker build on your PC. Bring it up first:

```bash
# repo root:
docker compose up -d --build
curl -s http://127.0.0.1:8099/api/health    # {"status":"ok",...,"database":"ok"}
# open http://127.0.0.1:8099
```

### A1. #200 - at-rest stored-key unlock (round-trip)
```bash
# 1) In the UI: Settings -> enable at-rest encryption, set a passphrase.
# 2) Store the key so it unlocks unattended, then restart:
#    add  HAFI_DB_KEY=<your-passphrase>  to the root .env, then:
docker compose up -d
curl -s http://127.0.0.1:8099/api/health     # expect database":"ok"  (unlocked)
# 3) Wrong key -> should come up LOCKED:
#    set  HAFI_DB_KEY=wrong  in .env ; docker compose up -d ; reload UI -> locked screen.
```
- [ ] Correct stored key -> comes up unlocked (`database":"ok"`).
- [ ] Wrong / empty key -> locked screen with a usable error message (not a bare 400).

Result: ________________________________________________

### A2. #372 - non-root container self-heals a root-owned volume (local repro)
Confidence check for the Pi behaviour (Claude already verified this locally).
```bash
VOL=homeassistant-expenses-analytics_finance_data
docker compose down
docker run --rm -v $VOL:/data alpine chown -R 0:0 /data     # simulate a root-owned mount
docker compose up -d
sleep 8
curl -s http://127.0.0.1:8099/api/health                    # expect database":"ok"
# app-created DB file is owned by uid 10001 (the app runs unprivileged):
docker run --rm -v $VOL:/data alpine stat -c '%u' /data/finance/finance.db   # expect 10001
```
- [ ] Boots healthy on a root-owned volume (self-heals via startup chown).
- [ ] The app-created DB file is owned by uid 10001 (app runs unprivileged).

Result: ________________________________________________

### A3. #370 - trust-header flag (standalone)
```bash
# With the flag OFF, identity is forced to the local owner (no header spoofing):
#   add  HAFI_TRUST_PROXY_HEADERS=false  to .env ; docker compose up -d
# Send a spoofed identity header - it must be ignored:
curl -s -H "X-Remote-User-Id: attacker" http://127.0.0.1:8099/api/users/me
```
- [ ] With the flag off, a spoofed `X-Remote-User-*` header does NOT change who you are (stays the local owner).

Result: ________________________________________________

### A4. #326 - CSP click-through
- [ ] Load http://127.0.0.1:8099, open devtools Console, click Dashboard / Transactions / Settings.
- [ ] No CSP violation errors; pages render and are interactive.

Result: ________________________________________________

### A5. UI smoke pass
Run [docs/ui-test-guide.md](ui-test-guide.md) (~20-30 min) to eyeball the v1.1.0
features (in-app modals, optimistic selects, forecasts, search filters + tokens,
audit CSV export, tag management, logs search/filters, demo staleness banner).
- [ ] UI smoke pass looks good.

Result: ________________________________________________

---

# Part B - Pi (Home Assistant add-on)

The add-on pulls a prebuilt image tagged to `addon/config.yaml`'s `version:`, so
to test v1.1.0 on the Pi first cut a release candidate:

1. Tell Claude **"cut v1.1.0-rc1"** -> the workflow builds `aarch64-...:1.1.0-rc1`.
2. On the Pi: HA -> Settings -> Add-ons -> HA Finance Intelligence -> update /
   reinstall to pull `1.1.0-rc1`. **Back up first** via the app's backup feature.
3. Run B1-B4 below.
4. When they pass, tell Claude **"cut v1.1.0"** for the real release.

### B1. #372 - writes the real Supervisor `/data` mount
- [ ] Add-on reaches "Started"; the ingress UI loads.
- [ ] Import a statement (or load demo data), **restart the add-on**, data is still there.
- [ ] Add-on log has no `unable to open database file` and no crash-loop.

Result: ________________________________________________

### B2. #370 - ingress login
- [ ] Open via the HA sidebar (ingress); you are logged in as the owner with no extra prompt.

Result: ________________________________________________

### B3. #200 - stored-key unlock via add-on Config
- [ ] Encrypt -> choose "stored" -> set `db_key` in the add-on Config -> restart comes up unlocked.
- [ ] A typo'd `db_key` -> locked screen.

Result: ________________________________________________

### B4. #342 - Caddy/TLS headers (only if you front it with a reverse proxy)
- [ ] `curl -I https://<your-host>/` returns 200 with the expected security headers; nothing leaks internal identity.

Result: ________________________________________________ (or N/A)

### B5. #453 - cold-start after long idle (opportunistic, not a blocker)
- [ ] If, after the Pi has been idle a while, the first Transactions click 404/400s then clears: open devtools Network and note the **exact status + URL** of the first failed request, paste it back.

Result: ________________________________________________ (or "not seen")

---

## Sign-off

- [ ] Part A blocks-release items (A1, A2, A3) pass.
- [ ] Part B blocks-release items (B1, B2, B3) pass.
- [ ] Manual/optional items (A4, B4, B5) pass or are N/A.
- [ ] Ready to cut `v1.1.0`.

Notes / issues found: ____________________________________
