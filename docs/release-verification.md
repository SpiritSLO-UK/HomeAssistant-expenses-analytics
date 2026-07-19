# Release verification checklist (v1.1.0)

**Part A (local) is fully automated** - every local check now runs in CI (green on
`main`) plus a live spot-check, so there is nothing to run by hand. **Part B still
needs the Raspberry Pi** (the Home Assistant add-on environment can't be
automated here). When Part B passes, tell Claude "checklist done, cut v1.1.0".

- Candidate: `main` (last release was v1.0.2, next is v1.1.0)
- Tester: ____________   Date: ____________

---

# Part A - Local - ✅ AUTOMATED (nothing to run by hand)

Each former manual check is covered by an automated CI job/test that is green on
`main`, and confirmed on the rebuilt demo. No action required.

| Check | Automated by | Result |
|-------|--------------|--------|
| **A1 #200** at-rest stored-key unlock (round-trip) | CI **Encryption restart** job (enable encryption → restart → assert unlocked) + live spot-check on the demo | ✅ correct key → `database:"ok"` (unlocked); wrong `HAFI_DB_KEY` → boots **locked** (`status:"locked"`), no crash-loop |
| **A2 #372** non-root container self-heals a root-owned `/data` | CI **Root-owned /data** job (boot on a `chown 0:0` volume; assert health + the app-created DB file is owned by uid 10001) | ✅ green on `main` |
| **A3 #370** trust-header flag | backend test `test_trust_proxy_headers.py` (flag off ignores a spoofed `X-Remote-User-*`) | ✅ green on `main`. (With the flag **on** - the add-on/ingress default - a spoofed header maps to a **pending, no-access** member, confirmed live.) |
| **A4 #326** CSP click-through | e2e `csp.spec.ts` (CSP header served; the **served inline theme-script hash** is present in `script-src` so drift fails CI; no CSP-violation console errors while navigating) | ✅ green on `main` (the earlier console violation was a stale build; clean on current code) |
| **A5** UI smoke pass | the **Playwright e2e suite** (55 tests / 11 specs: a render smoke of every page + self-cleaning task flows); HTML report attached to the GitHub Release | ✅ green on `main` |

> To re-run any of these yourself: they run automatically on every push (the "CI"
> workflow). The e2e HTML report is at `http://localhost:9323` after `cd e2e && npm run report`,
> or is attached to each GitHub Release.

**Part A sign-off:** ✅ nothing to do - automated + green.

---

# Part B - Pi (Home Assistant add-on) - MANUAL (needs the real Pi)

The add-on pulls a prebuilt image tagged to `addon/config.yaml`'s `version:`, so
to test v1.1.0 on the Pi first cut a release candidate:

1. Tell Claude **"cut v1.1.0-rc1"** -> the workflow builds `aarch64-...:1.1.0-rc1`.
2. On the Pi: HA -> Settings -> Add-ons -> HA Finance Intelligence -> update /
   reinstall to pull `1.1.0-rc1`. **Back up first** via the app's backup feature.
3. Run B1-B3 below (B4/B5 opportunistic).
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

- [x] **Part A** - automated in CI, green on `main` (+ A1 confirmed live). Nothing to run by hand.
- [ ] **Part B** blocks-release items (B1, B2, B3) pass on the Pi.
- [ ] Manual/optional items (B4, B5) pass or are N/A.
- [ ] Ready to cut `v1.1.0`.

Notes / issues found: ____________________________________
