# Security & data isolation

How the finance database is protected, who can read it, and the honest limits
of isolation inside Home Assistant. Addresses backlog items #5, #26, #27.

Related: [docs/privacy.md](privacy.md),
[spec §28 Security Requirements](../ha_finance_intelligence_spec.md).

## Where the data lives

| Path | What | Visibility |
|------|------|-----------|
| `/data/finance/finance.db` | SQLite database | **Private** to this add-on |
| `/data/finance/uploads/` | Imported CSV files | **Private** to this add-on |

`/data` is the add-on's **private persistent volume**. It is *not* the Home
Assistant `/config` share. This is a deliberate change from the original spec
(§26.4 suggested `/config/finance`): keeping the data in `/data` means:

- **No other add-on can read it.** Add-ons only see `/config` (or `/share`,
  `/ssl`, …) if they map those folders. Nothing maps another add-on's `/data`.
- **This add-on cannot read your wider HA config.** We removed the
  `homeassistant_config:rw` mapping, so the add-on has no access to
  `secrets.yaml`, other integrations' data, etc.
- It is still included in **Home Assistant backups** (so backup/restore works).

Trade-off: the database is no longer visible in the HA *File editor* add-on. If
you prefer that visibility over isolation, set `database_path` back to
`/config/finance/finance.db` and add `homeassistant_config:rw` to the add-on
`map:` - but understand that re-opens both directions of access.

## Layers of protection

1. **Add-on container isolation.** The app runs in its own container; `/data`
   is a private volume.
2. **No shared mappings.** `addon/config.yaml` maps no host folders. Network is
   ingress-only (`host_network: false`); the supervisor/HA/auth APIs are
   disabled (`hassio_api`, `homeassistant_api`, `auth_api` = false).
3. **File permissions.** `run.sh` sets the data directory to `700` and the
   database file to `600` (owner-only) on startup.
4. **AppArmor (optional).** `addon/apparmor.txt.example` confines the add-on to
   `/app` and `/data` and explicitly denies `/config`, `/ssl`, `/share`, etc.
   Enable it after validating on your own HA (see that file's header).
5. **Ingress auth.** The UI is served through Home Assistant ingress, so HA
   handles authentication - the app is not exposed on a public port.

### Standalone (no Home Assistant)

Running via Docker there is no ingress in front, so **you** own the network edge:
serve the app over **HTTPS** - a reverse proxy terminates TLS (see
[reverse-proxy.md](reverse-proxy.md)) - whenever it is reachable beyond
`localhost`, and keep the `finance_data` volume backed up. **Encrypted backups**
(passphrase, AES-256-GCM) let you keep an off-device copy safely; optional at-rest
encryption protects the live DB file.

### Standalone trust model - direct port exposure (CR-DOC-1)

**The shipped `docker-compose.yml` is `localhost`-only.** It publishes the app on
`8099:8099`, which binds the container port to **every** host interface with no
reverse proxy in front. This is fine on a single machine you reach over
`localhost`, but it is **not safe to expose to a wider network as-is**. The app
derives identity from the `X-Remote-User-*` headers and **bootstraps the first
user it sees as the household owner** - with the port reachable directly, anyone
who can hit it can send those headers, become owner, and read/write everything.

- **Behind Home Assistant ingress this is a non-issue** - HA authenticates the
  user and **strips any inbound `X-Remote-User-*`** a client tries to forge, then
  injects the real identity. The add-on maps no port and runs ingress-only
  (`host_network: false`). The caveat below applies **only** to direct/standalone
  exposure that bypasses ingress.
- **Standalone, before exposing beyond the host, do one of:**
  1. **Keep it `localhost`-only.** Bind the published port to the loopback
     interface (e.g. `ports: ["127.0.0.1:8099:8099"]`) so only the host can reach
     it. This is the default posture and needs no extra components.
  2. **Front it with an authenticating, header-stripping reverse proxy.** Put
     Caddy/nginx in front, have it terminate TLS, authenticate the user, and
     **strip every inbound `X-Remote-User-*` header** before proxying - injecting
     its own trusted identity. See [reverse-proxy.md](reverse-proxy.md) and the
     bundled `Caddyfile`. A proxy that forwards client-supplied identity headers
     unfiltered is **not** a mitigation.
  3. **Turn off header trust entirely.** Set `HAFI_TRUST_PROXY_HEADERS=false`
     (default `true`; CR-SEC-4 / #370). The app then **ignores** all
     `X-Remote-User-*` headers and resolves every request to the single `local`
     owner, so a direct peer can't forge an identity by sending them. This is the
     right switch for a single-user standalone box that has no proxy supplying
     identity - defence-in-depth on top of, not a replacement for, not exposing
     the raw port.

The default (`HAFI_TRUST_PROXY_HEADERS=true`) exists to preserve the HA-ingress
model, where a **trusted** proxy supplies identity. Leave it on only when such a
proxy is actually in front; otherwise prefer option 1 or 3 above.

## Access control, MFA & multi-user (Stage 12)

Identity is supplied by **Home Assistant ingress**, which sets
`X-Remote-User-Id` / `-Name` / `-Display-Name` on every request after HA has
authenticated the user. The app maps that to a `User` row. With no header
(standalone/dev) it falls back to a single local owner, so single-user installs
behave exactly as before.

- **Roles (RBAC).** `owner` (administrator), `member` (read/write), `viewer` and
  `child` (read-only). A user's role is **always read from their stored row** -
  never from the request - so a client can't assert a role by sending a header.
  A single `_auth_guard` middleware enforces: pending/disabled → 403, read-only
  roles → only safe (GET) methods, and user-management endpoints additionally
  require the `owner` role.
- **New-user approval.** The first user ever seen becomes the owner; everyone
  after appears **pending** with no data access until the owner approves them.
- **Last-owner guard.** The household's last active owner can't be demoted,
  disabled or deleted (you can never lock yourself out of administration).
- **Optional MFA (TOTP).** Per user, off by default. When on, a 6-digit code is
  required to open the app (a per-device session token, hashed in the DB -
  HMAC-SHA256 keyed on the app key when one is set, otherwise plain SHA-256 -
  with a 12-hour TTL) and a recently-entered code (within a **10-minute step-up
  window**) is required to confirm admin actions. Stored sessions are **capped
  per user** (oldest evicted) so repeated verifications can't grow the table
  without bound. The TOTP secret itself is **app-layer-encrypted at rest**
  (AES-256-GCM, keyed on `HAFI_DB_KEY`) whenever a key is set, falling back to
  plaintext only when none is. The MFA secret/sessions live inside the database,
  so the at-rest unlock (below) necessarily runs **first** and without MFA - the
  order is always *passphrase → then code*, never a deadlock.
- **MFA backup / recovery codes.** Enrolment issues a set of single-use backup
  codes (shown once; only their hashes are stored). Any one of them opens the app
  or disables MFA if you lose your authenticator - essential when MFA is
  *required* - and each code is spent on first use so it can't be replayed. You
  can regenerate the set at any time (which invalidates the old one). An owner can
  still disable/re-enrol MFA for another user as a fallback.
- **Failed-unlock visibility.** Because the DB is sealed during an unlock attempt,
  failures are recorded to a small file beside it and surfaced on the unlock
  screen and in the security-health panel.
- **Security-health panel.** An owner-only, non-nagging panel lists protections
  that are off (no at-rest encryption, no MFA, repeated failed unlocks, …) with a
  one-line fix; each item can be dismissed or snoozed.

> **Trust boundary - important.** Identity is only as trustworthy as the proxy in
> front of the app. The `X-Remote-User-*` headers are set by Home Assistant
> ingress, which **strips any inbound copy** a client tries to send - so through
> ingress a browser cannot forge them. This is **not** enforced by the app itself:
> **do not expose the add-on's port directly** - the add-on is ingress-only by
> default (`host_network: false`, no port mapped). If you bypass ingress and reach
> the app directly (or run the standalone compose without a header-stripping proxy),
> a client could forge those headers and impersonate any user. Keep it behind
> ingress (or a trusted reverse proxy).

## What this does NOT protect against (be honest)

- **Anyone with host / root access** to the machine running Home Assistant can
  read the file. Container isolation is not a defense against the host admin.
- **Home Assistant full backups** contain the database. Protect your backups
  (and any off-site copies) accordingly.
- **At rest, if you leave encryption off.** By default the database is plain
  SQLite, so anyone who can read the raw file (stolen disk, an untrusted backup
  destination) can read your data. **Optional at-rest encryption** (SQLCipher) is
  now available - Settings → "Database encryption" - and closes this gap when
  enabled; it's optional because the driver has no Windows wheel (it ships in the
  Linux add-on image on **both amd64 and aarch64/Raspberry Pi** - amd64 via the
  prebuilt wheel, aarch64 compiled from source in the image). Lost passphrase =
  unrecoverable.
- **Forged identity if you bypass ingress.** Identity comes from the HA ingress
  proxy headers. If you expose the add-on's raw port instead of going through
  ingress, a client could forge those headers and impersonate a user. Keep the
  add-on ingress-only (the default). See "Trust boundary" above.

## Testing never touches live data

The test suite forces a throwaway temp database and refuses to start otherwise
(`backend/app/tests/conftest.py`, `test_isolation.py`). See
[docs/privacy.md §4](privacy.md). (Backlog #30.)
