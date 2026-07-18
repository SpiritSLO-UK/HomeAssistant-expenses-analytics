# Release verification checklist (v1.1.0)

Fill this in as you go: tick each `- [ ]`, and write what you saw on the `Result:`
line. When the "blocks release" items all pass, tell Claude "checklist done" (or
paste the filled results back) and the final `v1.1.0` tag gets cut.

Legend: **[LOCAL]** = do it now on your PC against the docker demo, no Pi needed.
**[PI]** = needs the add-on installed on the Pi (see "Getting a testable image").

- Candidate: `main` (release prep done; last release was v1.0.2, next is v1.1.0)
- Tester: ____________   Date: ____________

---

## Getting a testable image for the Pi

The HA add-on pulls a prebuilt image tagged to `addon/config.yaml`'s `version:`.
To test v1.1.0 on the Pi before releasing it, cut a release candidate first:

1. Tell Claude "cut v1.1.0-rc1". That bumps `config.yaml` to `1.1.0-rc1` and tags
   it, so the release workflow builds `aarch64-...:1.1.0-rc1` images.
2. On the Pi: HA -> Settings -> Add-ons -> HA Finance Intelligence -> update /
   reinstall so it pulls `1.1.0-rc1`. (Back up first via the app's backup feature.)
3. Run the **[PI]** checks below against the RC.
4. When they pass, tell Claude "cut v1.1.0" for the real release.

The **[LOCAL]** checks below need no image; run them against the docker demo now.

---

## 1. Blocks release - must pass

### 1a. [PI] #372 - non-root container writes a real `/data` mount
The image runs as uid 10001 and chowns `/data` on startup. Confirm it boots and
persists on the Pi's Supervisor-mounted `/data`.

- [ ] Install/start the add-on on the Pi; it reaches "Started" and the ingress UI loads.
- [ ] Import a statement (or load demo data), then **restart the add-on**; your data is still there (proves `/data` is writable + persisted).
- [ ] Add-on log shows no `unable to open database file` and no crash-loop.

Result: ________________________________________________

### 1b. [PI] #370 - trust-header / ingress login
- [ ] Open the add-on via HA ingress (the sidebar item); you are logged in as the owner with no extra prompt.
- [ ] (Optional hardening) In add-on Config set `HAFI_TRUST_PROXY_HEADERS` off, restart: identity is forced to `local` (documented in `docs/security.md`).

Result: ________________________________________________

### 1c. At-rest "unlock every login" (#200) - stored-key round-trip
**[LOCAL] quick version (docker demo):**
```bash
# From the repo root, on your PC:
# 1) In the app (http://127.0.0.1:8099) -> Settings -> enable at-rest encryption, choose a passphrase.
# 2) Set the stored key so it unlocks unattended, then restart:
docker compose down
# add HAFI_DB_KEY=<your-passphrase> to the root .env, then:
docker compose up -d
curl -s http://127.0.0.1:8099/api/health   # expect {"status":"ok",...,"database":"ok"} (unlocked)
# 3) Now set a WRONG key and restart -> app should come up LOCKED:
#    edit .env HAFI_DB_KEY=wrong ; docker compose up -d ; reload the UI -> locked screen.
```
- [ ] Correct stored key -> comes up unlocked (`database":"ok"`).
- [ ] Wrong/empty key -> locked screen, no data exposed.

**[PI] version:** encrypt -> choose "stored" -> set `db_key` in the add-on Config -> restart comes up unlocked; a typo'd key shows the locked screen.

Result: ________________________________________________

---

## 2. Manual checks - do if applicable

### 2a. [LOCAL] #326 - CSP click-through
CI can't prove the SPA renders under the backend-served Content-Security-Policy.
- [ ] Load http://127.0.0.1:8099, open the browser devtools Console, click through Dashboard / Transactions / Settings.
- [ ] No CSP violation errors in the console; pages render and are interactive.

Result: ________________________________________________

### 2b. [PI/PROXY] #342 - Caddy TLS headers (only if you front the app with Caddy/nginx)
- [ ] `curl -I https://<your-host>/` returns 200 with the expected security headers (HSTS etc.); no header leaks the internal identity.

Result: ________________________________________________ (or N/A)

### 2c. [PI] #453 - cold-start after long idle (opportunistic)
Not a blocker; a retry+backoff mitigation shipped. If it recurs:
- [ ] After the Pi has been idle a while, first click on Transactions - if you get a 404/400 that clears after clicking around, open devtools Network and note the **exact status + URL** of the first failed request, and paste it back (helps confirm ingress-session-expiry vs cold backend).

Result: ________________________________________________ (or "not seen")

---

## 3. [LOCAL or PI] UI smoke pass

Run the click-through in [docs/ui-test-guide.md](ui-test-guide.md) (about 20-30
min) to eyeball the v1.1.0 features (in-app modals, optimistic selects, forecasts,
search filters + tokens, audit CSV export, tag management, logs search/filters,
demo staleness banner). The automated 51-test Playwright suite already covers
these on every CI run; this is your human sanity pass.

- [ ] UI smoke pass looks good (note anything odd below).

Result: ________________________________________________

---

## Sign-off

- [ ] All **section 1 (blocks release)** items pass.
- [ ] Section 2 items pass or are N/A.
- [ ] Ready to cut `v1.1.0`.

Notes / issues found: ____________________________________

When ticked, tell Claude "checklist done, cut v1.1.0".
