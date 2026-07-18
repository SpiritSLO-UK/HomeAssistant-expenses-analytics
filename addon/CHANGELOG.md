<!-- Home Assistant Supervisor reads the add-on changelog from this file
     (addon/CHANGELOG.md). Keep it in sync with the repo-root /CHANGELOG.md -
     the release process updates both. -->
# Changelog

All notable changes to HA Finance Intelligence. This project uses date-stamped,
human-readable entries; versions follow semantic versioning.

## v1.1.0 - Unreleased

A wide-ranging hardening, insight and polish release on top of v1.0.2. Data and
config carry over; database migrations run automatically on start.

> Provided "as is", no warranty, not financial advice - keep your own backups.

### What's new
- **Two-factor backup codes** - generate single-use recovery codes from
  Settings, so a lost authenticator doesn't lock you out.
- **Forecasts** - budgets show whether you're on pace for the period, projects
  show a burn-down against budget, and savings goals estimate time-to-goal from
  your deposit rate.
- **US bank statements import correctly** - month-first dates and US decimal
  formats are recognised, and each import profile can remember its date format.
- **Smarter search** - category and date filter tokens, tag-name matches, and
  keyboard navigation of results.
- **Tag housekeeping** - merge tags, see usage counts and clean up unused tags
  from Settings; vendors get a merge tool too.
- **Activity log** - search with actor and date filters, plus an owner-only
  CSV download.
- **Nicer dialogs** - in-app modals replace browser confirm/prompt popups, and
  dropdowns apply instantly (rolling back if a save fails).
- **Faster and steadier** - quicker dashboard and analytics queries, a bigger
  database connection pool, and fewer transient errors after the app has sat
  idle.
- **More secure** - the container runs as a non-root user, AI calls are
  rate-limited and budget-capped, stronger security headers, and a broad
  hardening pass across the backend.
- **Better tested** - 993 backend tests plus a browser test suite that clicks
  through every page.

### Upgrade notes
- One new database migration (`mfa_backup_codes`) runs automatically on start.
- If at-rest encryption is enabled (`HAFI_DB_KEY` set), you may be asked to
  verify two-factor once after upgrading.
- Upgrading from v1.0.1 or older also brings everything in v1.0.2 below,
  including the fix for the "unrecognised date" error when importing US-format
  CSV statements.

## v1.0.2 - 2026-06-29

A correctness, security-hardening and quality release on top of v1.0.1, with
broader bank/receipt import support. Data and config carry over; database
migrations run automatically on start.

> Provided "as is", no warranty, not financial advice - keep your own backups.

### Added
- **Curve support** - imports the real Curve app export, recognises **Curve Cash**
  (cashback) rewards, and **de-duplicates** spend that also appears on the
  underlying funding card's own statement (kept-but-flagged when uncertain).
- **Barclaycard** statement parser.
- **Define-your-own CSV import** - map the columns of any bank's CSV export.
- **Receipts → transactions** - suggests the matching transaction for an unmatched
  receipt, with one-click *Add* from the Review Queue.
- **Bulk AI suggest + categorise** from the Review Queue.
- **AI extraction from PDF** receipts/invoices - the first page is rendered for the vision model (not images only).

### Improved
- **Spending-by-location** recognises more currencies (CZK/HUF/TRY/BRL/ISK/KRW/ILS/…).
- **More honest, more robust UI** - typed API errors with the two-factor session
  header on every request (uploads/downloads/restore now work under MFA),
  localised currency formatting, inline error banners, an app-wide error boundary,
  inputs that don't lose edits on a background refresh, and confirm dialogs on
  destructive actions.
- Subscriptions ("due in N days" no longer shows *null*), budgets (100% spent now
  reads as *over*), tags, savings/investment charts, and search refinements.

### Fixed
- Split transactions now total the parent **to the penny** in base currency.
- A no-op categorisation rule no longer blocks a lower-priority rule.
- FX rates are matched case-insensitively (no duplicate rate rows).
- A spurious pending **"Local User"** is no longer created by the internal health probe.
- Numerous smaller correctness fixes across imports, receipts and the dashboard.

### Security
- **Owner-gated** backup / restore / config import; config-import **allow-list**;
  **SSRF guard** on user-supplied URLs; **one-time** TOTP codes + MFA **lockout**
  throttle; upload-size and parser **DoS caps**; tighter **CORS**; and tighter
  handling of the at-rest encryption **stored key**.

### Internal
- Cleared the SonarCloud backlog (security findings, code smells, cognitive
  complexity) and refreshed dependencies. No behaviour change.

## v1.0.1 - 2026-06-07

A polish release from real-world testing on top of v1.0.0 - quality-of-life
features, faster search at scale, a more honest UI, and several fixes. Data and
config carry over; database migrations run automatically on start.

> Provided "as is", no warranty, not financial advice - keep your own backups.

### Added
- **Manage accounts** - create, rename, delete (when empty) or **merge** accounts,
  and a new **Debit account** type.
- **Settings → Storage & statistics** - database size on disk plus AI-call tallies
  (cloud vs local, completed/failed, average turnaround).
- **Choose what to publish to MQTT** - per-group *and* per-sensor selection; a
  disabled sensor is removed from Home Assistant (its retained discovery is cleared).
- **Reuse a receipt's AI-extracted category** for the transaction it matches - no
  second AI call.
- **Two-factor controls for admins** - require/scope MFA per user (optional vs
  required; app vs app+admin), an enrolment gate, and per-user page access.

### Improved
- **Much faster transaction search at scale** - a SQLite FTS5 (trigram) index makes
  substring search near-instant on very large datasets (falls back to the previous
  behaviour where unavailable); the Transactions search box now debounces.
- **Logs** - AI & privacy **decisions** are grouped on their own in the action
  filter, and each decision kind (AI mode / OCR / FX / image-sent) is individually
  filterable.
- **Receipts** - "View original" now **previews in a popup** instead of downloading.

### Fixed
- Two-factor not prompting on a fresh open; removed a duplicate AI audit-log table.
- Mobile **bank-CSV** files no longer greyed-out in the import file picker.
- Receipt OCR no longer dumps **card-payment-slip** terminal text into the merchant
  field (a clean name or nothing → review).
- Corrected the Home Assistant **add-on repository** install steps (the missing
  "Add" step) in the README and docs.
- A receipt-file accessibility fix (keyboard-dismissable preview dialog).

### Docs
- A new **screenshot gallery** ([docs/screenshots.md](docs/screenshots.md)) plus a
  README hero/grid and refreshed community-intro post.
- Documented that **the app learns** - categorisation goes manual → learned rules →
  vendor defaults → keyword library → (opt-in) AI, so over time there's less manual
  tidying and **fewer AI calls**.
- The **local-LLM** path is noted as built-but-untested, with a call for feedback.

## v1.0.0 - 2026-06-05

The **1.0 release** - and the point the project becomes **Home Assistant-first**.
Everything from the beta, now installable on Home Assistant as a first-class
**add-on**: a one-click repository add, a prebuilt multi-arch image (amd64 +
aarch64/Raspberry Pi - no on-device build), ingress single sign-on, MQTT sensors,
and an energy-cost offset that nets your solar/grid production against your energy
bill. Validated end-to-end on a Raspberry Pi 4.

> Provided "as is", no warranty, not financial advice - keep your own backups.

### Added - Home Assistant
- **Add-on install** from a prebuilt GHCR image via an HA **add-on repository**
  (one-click "Add repository" badge); the Supervisor pulls the image - fast even
  on a Raspberry Pi, no on-device build.
- **Ingress SSO** - open from the HA sidebar; your Home Assistant identity signs
  you in, and the first user becomes the owner.
- **MQTT discovery sensors** - spend / income / net / review / uncategorised /
  subscriptions, plus per-budget and per-project, auto-created as an HA device.
- **Energy-cost offset** - read solar/grid production from Home Assistant
  (`ha_api`) or MQTT and net it against your energy-bill spend, with a
  production/saving **trend over time**.
- **At-rest database encryption** on both shipped architectures (SQLCipher; opt-in).

### Changed / fixed (since v0.9.5-beta)
- Receipts-first **Import** page - a dedicated receipt uploader on top - plus a
  friendlier "this looks like a receipt" recovery when a statement image can't be read.
- `index.html` is served `no-cache` so add-on updates load on next open (no full
  Home Assistant restart needed).
- AI **✨ suggest** proposes category + country + vendor in one step; a **decision
  audit log** records every privacy-relevant change and every image sent to AI.
- Many UX fixes surfaced by release-candidate testing on real Home Assistant hardware.

## v0.9.5-beta - 2026-06-04

The practical **close of beta development**. Everything the first beta listed as
"not in this release" has since landed, plus a wave of depth and polish. Still
standalone-first; the **Home Assistant add-on packaging is the next release**
(and the point at which the branch strategy switches - `main` becomes the HA
release line).

> Same beta caveat: provided "as is", no warranty, not financial advice - keep
> your own backups.

### Added
- **Investments & pensions** - distinct models: an *investment* account is
  **holdings-first** (tickers × price → market value, unrealised gain, value-over-
  time chart with day/month/year change) while a *pension* tracks a **statement
  value** with contributions/withdrawals. Optional, off-by-default **price feed**
  (keyless Stooq or keyed Alpha Vantage; only ticker symbols leave the box).
- **Cars, home & assets** - a car uses one consistent unit system (imperial MPG or
  metric L/100km) with refuel/economy history; a home tracks utility-meter readings
  → usage & cost; plus maintenance/running-cost logs.
- **Spending by location** - a world/geo map ranks the month's spend by country
  (per-transaction country → vendor country → currency fallback), with a
  `set_country` **rule action** and per-trip/per-vendor country overrides.
- **Paperless-ngx import** - pull documents from your own Paperless into receipts
  (outbound-only; off until a URL + env token are set).
- **Over-time charts** with a 6M/1Y/2Y/5Y range selector across Investments,
  Savings, Travel and Projects; Business gets a year scope.
- **"Needs attention"** dashboard card (review queue + uncategorised + FX-needed)
  and a Review-page **Uncategorised** tab with inline quick-categorise.
- **AI re-process** - re-run the model over already-categorised rows to find better
  matches (suggest-only; never overwrites a manual choice).
- UI: **dark mode**, **persisted filters**, **re-orderable + hideable** sidebar
  tabs and dashboard cards, resizable columns, an **About & source** card.

### Changed / fixed
- HTTPS for the standalone deployment via a bundled Caddy reverse-proxy profile.
- Receipt re-uploads now report **"already imported"** (content-hash dedup).
- Settings → **Services** split into per-service panels; the Receipts Paperless
  card only appears once configured.
- Performance indexes on hot columns; dependency refresh; docs pack.
- Tests run **across all CPU cores** (`pytest-xdist`); ~405 backend tests.
- SonarCloud quality gate green (security hotspots reviewed; code smells cleared).

## v0.9.0-beta - 2026-06-03

First public **beta**. A complete, privacy-first personal-finance app you can run
**standalone** (Docker) today. Home Assistant integration (one-click add-on
install, MQTT sensors, ingress SSO) is scaffolded but **not part of this release** -
it lands in a later version.

> Beta caveat: provided “as is”, no warranty, not financial advice. Keep your own
> backups. Review before relying on it.

### Run it (standalone, no Home Assistant)

```bash
docker compose up -d --build
# then open http://localhost:8099
```

Data (SQLite DB + uploads + safety backups) is kept in the `finance_data` volume.

### Highlights

- **Import** bank/card statements (CSV, PDF - incl. scanned-PDF OCR - and
  photos/scans), with duplicate detection and an "already imported" guard.
- **Categorise** automatically (rules → vendor → keyword) with an in-app rules
  guide; **split** transactions; **projects**, **tags**, **budgets** (weekly→yearly).
- **Dashboard**: customisable, reorderable cards - spend/income/net, trends,
  heads-up alerts, by category / vendor / project / member, plus per-domain
  summaries (savings, budgets, business, travel, allowance) and a processing card.
- **Travel** (foreign-currency trips), **Business/VAT**, **Savings** (balances,
  goals, interest), **Subscriptions** (auto-detected), **Review queue**.
- **Multi-user & roles** (owner/member/viewer/child), shared vs private accounts,
  kids' allowance, a **setup wizard**, and **per-user "manage settings"** grants.
- **Multi-currency** with manual or opt-in online (ECB/Frankfurter) rates.
- **Global search**, **receipt attach + viewer**, **resizable tables**,
  **CSV export**, **demo data** (load and remove).
- **Privacy & security**: strict-local by default; **AI is opt-in**, redacted,
  audited, with per-category never-cloud blocking and a one-time disclaimer;
  optional TOTP MFA + admin step-up; data-retention policies; backup/restore and
  optional at-rest encryption.
- A **Services** panel to switch AI / OCR / online-FX on and off from one place.

### Not in this release

- Home Assistant add-on install / ingress SSO / MQTT sensors (scaffolded under
  `addon/`, shipping later).
- Stretch items: investments & pensions, HA energy-cost offset, Paperless import,
  geographic spend map, per-context (car/home) dashboards.

### Under the hood

- Python 3.12 · FastAPI · SQLAlchemy 2.0 · Pydantic v2 · Alembic.
- React 18 · TypeScript · Vite 5 · TanStack Query.
- 336 backend tests; CI runs ruff + pytest + a frontend type-check/build.
