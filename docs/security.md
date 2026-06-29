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
`map:` — but understand that re-opens both directions of access.

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
   handles authentication — the app is not exposed on a public port.

### Standalone (no Home Assistant)

Running via Docker there is no ingress in front, so **you** own the network edge:
serve the app over **HTTPS** — a reverse proxy terminates TLS (see
[reverse-proxy.md](reverse-proxy.md)) — whenever it is reachable beyond
`localhost`, and keep the `finance_data` volume backed up. **Encrypted backups**
(passphrase, AES-256-GCM) let you keep an off-device copy safely; optional at-rest
encryption protects the live DB file.

## Access control, MFA & multi-user (Stage 12)

Identity is supplied by **Home Assistant ingress**, which sets
`X-Remote-User-Id` / `-Name` / `-Display-Name` on every request after HA has
authenticated the user. The app maps that to a `User` row. With no header
(standalone/dev) it falls back to a single local owner, so single-user installs
behave exactly as before.

- **Roles (RBAC).** `owner` (administrator), `member` (read/write), `viewer` and
  `child` (read-only). A user's role is **always read from their stored row** —
  never from the request — so a client can't assert a role by sending a header.
  A single `_auth_guard` middleware enforces: pending/disabled → 403, read-only
  roles → only safe (GET) methods, and user-management endpoints additionally
  require the `owner` role.
- **New-user approval.** The first user ever seen becomes the owner; everyone
  after appears **pending** with no data access until the owner approves them.
- **Last-owner guard.** The household's last active owner can't be demoted,
  disabled or deleted (you can never lock yourself out of administration).
- **Optional MFA (TOTP).** Per user, off by default. When on, a 6-digit code is
  required to open the app (a per-device session token, SHA-256-hashed in the DB,
  12-hour TTL) and a recently-entered code (within a **10-minute step-up window**)
  is required to confirm admin actions. The MFA secret/sessions live inside the
  database, so the at-rest unlock (below) necessarily runs **first** and without
  MFA — the order is always *passphrase → then code*, never a deadlock.
  There are currently **no backup/recovery codes**: if you lose your authenticator,
  another owner can disable/re-enrol MFA for you (and a sole owner restores from a
  backup), so keep a backup.
- **Failed-unlock visibility.** Because the DB is sealed during an unlock attempt,
  failures are recorded to a small file beside it and surfaced on the unlock
  screen and in the security-health panel.
- **Security-health panel.** An owner-only, non-nagging panel lists protections
  that are off (no at-rest encryption, no MFA, repeated failed unlocks, …) with a
  one-line fix; each item can be dismissed or snoozed.

> **Trust boundary — important.** Identity is only as trustworthy as the proxy in
> front of the app. The `X-Remote-User-*` headers are set by Home Assistant
> ingress, which **strips any inbound copy** a client tries to send — so through
> ingress a browser cannot forge them. This is **not** enforced by the app itself:
> **do not expose the add-on's port directly** — the add-on is ingress-only by
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
  now available — Settings → "Database encryption" — and closes this gap when
  enabled; it's optional because the driver has no Windows wheel (it ships in the
  Linux add-on image on **both amd64 and aarch64/Raspberry Pi** — amd64 via the
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
