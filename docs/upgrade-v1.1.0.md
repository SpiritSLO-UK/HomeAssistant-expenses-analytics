# Upgrade guide: v1.0.2 to v1.1.0

A short, practical checklist for moving an existing install from **v1.0.2** to
**v1.1.0**, for both deployment paths (Home Assistant add-on and standalone
Docker). Your data and config carry over; the only database change is additive
and runs automatically on start. For the full list of what changed, see the
[CHANGELOG](../CHANGELOG.md).

> Provided **"as is", without warranty**, and **not** financial advice. You are
> responsible for your own backups and for verifying figures before relying on
> them.

## Before you start: take a backup

The upgrade is low-risk (see [Rollback](#rollback) below), but the safest habit
is to grab an off-device copy first. In the app go to **Settings → Backups** and
create an **encrypted backup**, or, on Home Assistant, take a full **HA backup**
(Settings → System → Backups) which includes the add-on's `/data`.

## What you must know

Most of v1.1.0 is transparent - you update and carry on. Four items are worth
reading before you upgrade; only the first two touch users at all, and only in
specific setups.

### 1. New database migration (automatic)

v1.1.0 adds one migration, `d3e4f5a6b7c8` (**mfa_backup_codes**), which creates
the storage for two-factor backup/recovery codes. It is **additive and
non-destructive** - it adds new storage and touches none of your existing data.

- **Add-on:** it runs automatically on start (the add-on runs `alembic upgrade
  head` in `run.sh` before the app comes up). Nothing to do.
- **Standalone:** the container applies pending migrations on boot, so it runs
  the first time you start the v1.1.0 image. Nothing to do.

### 2. One-time two-factor re-verify (only if at-rest encryption is on)

If you run with at-rest encryption and a **stored key** (`HAFI_DB_KEY` set), and
you have **two-factor enabled**, you may be asked to **verify two-factor once**
after upgrading. In v1.1.0 the TOTP secret is re-wrapped with application-layer
encryption, and re-enrolment / re-verification invalidates existing sessions, so
you sign the second factor once and then continue as normal. Keep your
authenticator (or a backup code) to hand for the first sign-in after the update.

If you do not use at-rest encryption, or do not use two-factor, this does not
apply to you.

### 3. Non-root container (nothing to do)

The image now runs as an **unprivileged user (uid 10001)** instead of root
(#372, #446). On start it fixes ownership of `/data` once as root, then drops
privileges to uid 10001 for everything else. This is automatic and self-healing:

- A previously **root-owned `/data` volume** is chowned once on first start of
  v1.1.0, so existing installs keep working with no action from you.
- If you had **manually chowned** the volume yourself, that still works too - the
  ownership fix is best-effort and skips a volume that is already correct or
  read-only.

There is nothing for you to change.

### 4. New AI-endpoint guard knobs (power users only)

v1.1.0 adds ceilings on the AI-dispatching endpoints to protect against a
hostile-but-authenticated caller. The **defaults are generous** and normal
single-user use never reaches them, so you can ignore these unless you want to
tune them. Each is an environment variable (`HAFI_` prefix); **`0` disables**
that guard.

| Variable | Default | What it caps |
|----------|---------|--------------|
| `HAFI_AI_RATE_LIMIT_PER_MINUTE` | `30` | Per-user AI requests per minute (over the limit returns 429). |
| `HAFI_AI_MAX_PAYLOAD_BYTES` | `102400` (~100 KB) | Max request-body size on those routes (over returns 413). Raw image uploads keep their own separate cap. |
| `HAFI_AI_DAILY_REQUEST_CAP` | `500` | Max AI requests per UTC day (over returns 429). |

On the add-on these are internal defaults; set them as add-on options / env only
if you have a specific reason. On standalone, add them to your `.env` (or the
compose `environment:` block) if you want to change them.

### Standalone hardening: `HAFI_TRUST_PROXY_HEADERS`

Not new behaviour to configure at upgrade time, but if you expose the standalone
app beyond `localhost`, v1.1.0 makes proxy-header identity trust an explicit,
opt-in decision (#370). Reverse-proxy `X-Remote-User-*` headers are only honoured
when `HAFI_TRUST_PROXY_HEADERS` is on; set it to `false` on a directly exposed
port so identity can't be spoofed. See **[security.md](security.md)** for the
full trust model and the header-spoof caveat.

## How to upgrade

### Home Assistant add-on

When v1.1.0 is published, the add-on shows an **Update** button (Supervisor pulls
the new image):

1. **Settings → Add-ons → HA Finance Intelligence**.
2. Click **Update** and wait for Supervisor to pull and restart the add-on.
3. Open the Web UI. If you use at-rest encryption + two-factor, complete the
   one-time re-verify (item 2 above).

Your data in `/data` is preserved; the migration runs automatically on start.

### Standalone (docker-compose)

```bash
git pull
docker compose pull               # fetch the new image if you use a published tag
docker compose up -d --build      # rebuild / restart on the new version
```

The `finance_data` volume carries over and the container applies the migration on
boot, so your data and config survive the upgrade.

## Rollback

Your data lives in the `/data` volume (`finance_data` for standalone), separate
from the image, so downgrading swaps the code and leaves your data in place. The
v1.1.0 migration is **additive**, so a downgrade to v1.0.2 is low-risk: the older
code simply ignores the new backup-codes storage.

- **Add-on:** reinstall / roll back to the previous version from the Supervisor,
  or restore the HA backup you took first.
- **Standalone:** check out the previous release (`git checkout v1.0.2`) and
  `docker compose up -d --build`, or restore an encrypted backup from the app.

Even though rollback is low-risk, restoring from the backup you took before
upgrading is always the cleanest path if anything looks wrong.

## See also

- [CHANGELOG](../CHANGELOG.md) - the full v1.1.0 entry.
- [Install on Home Assistant](ha-install.md) - add-on install and options.
- [Standalone (docker-compose)](standalone.md) - env-var config and everyday
  commands.
- [Security & isolation](security.md) - trust model, `/data` volume, encryption
  at rest.
