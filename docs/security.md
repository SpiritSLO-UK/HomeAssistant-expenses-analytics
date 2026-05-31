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

## What this does NOT protect against (be honest)

- **Anyone with host / root access** to the machine running Home Assistant can
  read the file. Container isolation is not a defense against the host admin.
- **Home Assistant full backups** contain the database. Protect your backups
  (and any off-site copies) accordingly.
- **Unencrypted at rest.** The database is currently plain SQLite. True
  protection against someone reading the raw file (e.g. a stolen disk or an
  untrusted backup destination) requires **encryption at rest** with a
  master key — that is backlog item #15 and is deferred pending a decision on
  key management (where the key lives, how it's entered on restart). When added,
  it will pair with the encrypted/cloud backup feature.

## Testing never touches live data

The test suite forces a throwaway temp database and refuses to start otherwise
(`backend/app/tests/conftest.py`, `test_isolation.py`). See
[docs/privacy.md §4](privacy.md). (Backlog #30.)
