# Changelog

All notable changes to HA Finance Intelligence. This project uses date-stamped,
human-readable entries; versions follow semantic versioning.

## v1.2.2 - 2026-08-07

> Provided "as is", no warranty, not financial advice - keep your own backups.

A small enhancement release on top of v1.2.1. Migrations unchanged; data and
config carry over.

### Added
- **Config & library export now includes vendor default categories and rules.**
  Settings → Data → **Config & library** previously exported only your categories,
  vendor aliases and settings, so moving to another instance lost each vendor's
  default category and every rule you had built. The export (now v0.2) also carries
  each vendor's **default category** and your entire **rules** list. References are
  stored by name rather than internal id, so they resolve correctly on the target;
  a rule whose category, vendor or project is absent there is skipped and reported
  rather than imported with a broken link. Older (v0.1) exports still import
  unchanged, and import stays a non-destructive merge that never deletes.

## v1.2.1 - 2026-07-23

> Provided "as is", no warranty, not financial advice - keep your own backups.

A small feature + fix release on top of v1.1.0. Migrations unchanged; data and
config carry over.

### Added
- **Re-apply rules to existing transactions** - the Transactions page gains a
  "Re-apply rules" panel that re-runs your rules (plus vendor and keyword
  matching) over all transactions or just the current filter, with a dry-run
  preview of how many will change. A matching rule overrides an auto-assigned
  category (e.g. a keyword-guessed *Cash*); your manual picks are kept unless you
  opt in to "also replace my manual choices". Previously "Re-categorise" only ever
  touched rows that had no category yet, and the panel now guides you to the
  "replace existing" option when a filtered set is already categorised.
- **Delete all / delete matching a filter** - remove every transaction matching
  the current filter (or all of them) in one action, instead of ticking 50 at a
  time page by page. Offered on the Transactions list once a whole page is
  selected (for any active filter, so you can wipe just that subset, or the whole
  set), and as a "Delete all transactions" card in Settings > Data. Owner only,
  gated by a fresh two-factor code, and a timestamped safety backup is taken first
  (restore it from Backup & restore); accounts, categories, rules and settings are
  kept.

### Fixed
- **In-app version no longer drifts** - the sidebar badge and `/api/health`
  reported `v1.0.2` after v1.1.0 shipped, because the release bumped
  `addon/config.yaml` but not the backend/frontend version files. They are back
  in lockstep; a `bump-version` script now sets them together and a CI check fails
  the build if they ever disagree (or don't match a release tag).
- **A stale rule no longer breaks a later import** - a rule whose target category,
  vendor or project was deleted *after* the rule was created used to write a
  dangling reference onto matching transactions, which failed on save and could
  return a 500 on the next import or "Load demo data". Such a `set_category` /
  `set_vendor` / `set_project` action now simply does nothing when its target is
  gone, instead of erroring.

## v1.1.0 - 2026-07-19

> Provided "as is", no warranty, not financial advice - keep your own backups.

### Highlights

A wide-ranging hardening, insight and polish release on top of v1.0.2 - over 250
pull requests. Two-factor gains single-use **backup/recovery codes**; the
container now runs as a **non-root user**; budgets, projects and savings goals
get **forecasts** (pace, burn-down, time-to-goal) and can be **edited after
creation**; the sidebar becomes a **customisable grouped navigation**; US bank
statements import correctly; dates display in your chosen format app-wide; native
browser popups are replaced by an **in-app modal system**; and a systematic
correctness-and-performance review swept roughly 30 backend services. Data and
config carry over; database migrations run automatically on start.

### Security & hardening
- **MFA backup/recovery codes** - generate single-use codes from a new Settings
  card (remaining count, copy/download), so a lost authenticator doesn't lock
  you out.
- **Non-root container** - the image runs as an unprivileged user (uid 10001)
  with digest-pinned base images and SHA-pinned CI actions; on start it fixes
  `/data` ownership once as root, then drops privileges, so existing installs
  keep working.
- **Two-factor internals** - the TOTP secret is encrypted at the application
  layer at rest, re-enrolment invalidates existing sessions, sessions per user
  are capped, and session tokens are HMAC-hashed.
- **AI endpoint guards** - the AI gateway gets a rate limit, a payload-size cap
  and a daily cloud-spend budget; provider endpoint URLs are scheme-validated;
  the never-cloud category gate now also covers uncategorised rows, and vision
  requests refuse a silent auto-fallback to cloud.
- **AI key on standalone** - the AI provider API key can now be set from
  Settings on a standalone install and is stored **encrypted at rest** (the same
  application-layer crypto as the two-factor secret); the `HAFI_AI_API_KEY`
  environment variable still takes precedence, and the key is never returned by
  the API or written to logs.
- **SSRF guard hardened against DNS rebinding** - for outbound calls to
  user-supplied URLs (AI providers, Paperless) the host is resolved once, every
  resolved address must be public, and the connection is made to that validated
  IP with the original host name preserved, so the name can't be re-resolved to
  a private address between the check and the request.
- **Proxy-header trust is opt-in** - reverse-proxy identity headers are only
  honoured behind an explicit flag, so a directly exposed port can't be
  identity-spoofed.
- **Middleware gating fixes** - gate-exempt path prefixes match on segment
  boundaries only, and the member roster is role-gated at the route.
- **Headers & responses** - a backend-served Content-Security-Policy, a full
  security-header block (incl. HSTS) in the bundled Caddy profile, and unknown
  `/api/*` paths return 404 instead of the app page.
- **Encrypted-DB safety** - the SQLCipher key is applied without passphrase
  interpolation, an encrypted copy is verified before it replaces the plaintext
  database, wrong-passphrase and missing-driver errors are distinguished, and
  encrypted backups enforce a passphrase strength floor. The locked screen now
  shows the server's actual error, and an empty passphrase is rejected with a
  clear message instead of a generic failure.
- **Security-health card** - new checks: stored key without MFA, stale backups,
  and settings managers without MFA.
- **Security events to MQTT (opt-in)** - failed unlocks, failed two-factor
  attempts and wrong-passphrase events can be published to MQTT so Home
  Assistant can alert on them; off by default, enabled via the
  `mqtt_security_events` add-on / `HAFI_MQTT_SECURITY_EVENTS` env option (the
  same place MQTT itself is turned on).
- Broader PII redaction, OCR decompression-bomb and page-budget guards, capped
  audit-row size, and household-scoped audit queries.

### Money pipeline & import
- **US statements import correctly** - month-first dates and US/EU decimal
  money formats are parsed across receipts and generic CSVs, detected per file,
  with an explicit override when every date in a file is ambiguous (plus
  regression tests pinning the behaviour).
- **Import profiles remember a date format** (auto / day-first / month-first)
  with a selector in the import flow; saved CSV profiles are selectable again
  and the column-mapping UI is clearer.
- **Faster imports** - dedup hashes batched, rules and vendor aliases preloaded,
  and the "already imported" report corrected.
- **Receipts** - refund receipts match credit transactions, match candidates are
  properly scoped (and the sole original is never dropped on auto-match), and
  card fields re-sync when OCR results arrive.
- **Paperless-ngx** - HTTP calls retry with bounds, and re-import back-fills OCR
  for documents that missed it.

### Insights & forecasting
- **Budget pace** - budget summaries show a prorated pace signal: are you ahead
  of or behind where you should be at this point in the period?
- **Project burn-down** - projects get a run-rate forecast against budget.
- **Savings goals** - a deposit-rate forecast with an estimated time-to-goal,
  plus a compound-interest projection on savings history.
- **Subscriptions** - fortnightly and bi-monthly cadences are detected, price
  rises no longer break detection, and the page gains sort/filter and an
  annualised total.
- **Storage & statistics** - per-table row counts with a largest-table
  indicator.

### UI & UX
- **In-app modal system** - proper in-app dialogs replace native browser
  confirm/prompt/alert popups everywhere.
- **Optimistic selects** - dropdown/select controls apply instantly and roll
  back with an error if the save fails.
- **Search** - `category:` and date filter tokens (advertised on the Search
  page), tag-name matches, deep-linked result chips, and keyboard navigation of
  grouped results with Enter-to-open.
- **Customisable grouped navigation** - the sidebar's 24 pages are now organised
  into groups (Money, Library, Wealth, Plans, System, plus standalone Dashboard /
  Search / Energy), and each group page shows sub-tabs across the top to switch
  between its members. A "Customise navigation" editor lets any (non-child) user
  rename, show/hide, reorder, move pages between groups, and create their own
  groups - saved per user. The long **Settings** page is likewise split into
  General / AI & privacy / Security / Integrations / Data sub-tabs, each
  shareable/deep-linkable via a `?section=` URL.
- **Tag management** - merge tags, see usage counts and clean up unused tags
  from a dedicated **Tags** page in the sidebar.
- **Edit after creation** - projects, budgets, rules and savings goals can be
  edited after they are created (and a savings account's name/institution),
  instead of delete-and-recreate.
- **App-wide date format** - a Settings toggle picks how dates display
  everywhere (ISO `2026-07-18`, US `07/18/2026` or UK `18/07/2026`).
- **Export what you see** - the Transactions CSV export honours your ticked
  selection, and can export the whole filtered set rather than just the current
  page.
- **Undo a bulk edit** - after applying a category / project / country / business
  change to many transactions at once, an **Undo** restores each row's previous
  value.
- **Activity log** - server-side search with actor and date-range filters, and
  an owner-only audit-log CSV export.
- **Quality-of-life** - a vendor merge UI plus a category merge where you choose
  which one is **kept**, clone + drag-to-reorder for rules, a split editor with a
  penny-exact **amount/percentage** toggle, bulk recolour of categories with a new
  category defaulting to an unused colour, AI-batch select-all + CSV export, a
  receipt viewer with **zoom / rotate**, an **"all history"** option on the
  over-time charts, and heads-up dashboard alerts you can enable, disable or clear.
  Search keyboard navigation now stays out of the way until you actually press an
  arrow key.
- **Cloud AI batch runs in the background** - sending a cloud AI categorisation
  batch no longer blocks; it runs in the background with live
  sent/received progress you can watch.
- **Consistent budgets/projects/savings** - the three pages now share one
  progress-bar and list-row style, so budgets, projects and savings goals read
  the same way; a budget row also shows one clear pace signal instead of several
  overlapping labels.
- **Accessibility** - remaining unlabeled form controls, AI/receipt dialogs and
  map points received accessible names; high-signal form fields also carry
  stable `name` and `autocomplete` attributes so browser autofill and password
  managers behave; charts scale responsively.
- **Faster first load** - the frontend vendor bundle is code-split.

### Reliability & performance
- **Bigger database connection pool** so request bursts (a dashboard opening
  many cards at once) don't error, and the frontend retries transient
  cold-start failures after the app has sat idle.
- Blocking OCR / AI / statement-parse work moved off the API event loop.
- Dashboard, analytics, business and project aggregates pushed down into SQL
  `GROUP BY`; N+1 queries fixed in savings and projects; per-account savings
  history batched into one call.
- A systematic **correctness-and-performance review pass across roughly 30
  backend services** (accounts, allowance, audit, backups, energy, FX,
  household, MQTT, Paperless, prices, retention, review queue, scope, settings,
  subscriptions, travel, vendors and more), fixing edge cases and tightening
  queries.
- Backup/restore is quiesced and atomic; price feeds get bounded retries and
  rate-limit detection; the energy offset falls back to the last snapshot when
  MQTT isn't live.

### Testing & docs
- **1,169 backend tests**, plus a new **Playwright browser suite** (80+
  tests across 24 specs: a render smoke of every page and end-to-end task flows,
  including the MFA backup-codes flow with the TOTP computed in-test) with an HTML
  report attached to CI runs and releases, and a step-by-step UI test
  walkthrough. CI additionally guards the non-root container, encryption
  restart, and Content-Security-Policy hash drift. A public **ROADMAP.md** shows
  where the project is heading.
- Distinct per-package READMEs for the add-on vs standalone, an AI-gateway and
  privacy-gate data-flow diagram, refreshed architecture docs, and a documented
  standalone trust model (including the proxy header-spoof caveat).
- Docs now state plainly that the database is plaintext unless `HAFI_DB_KEY`
  at-rest encryption is enabled.

### Fixes (from a pre-release code-review pass)
A systematic critical review before release found and fixed 30 issues; the
notable ones:
- **Backups on encrypted databases** - downloading a database backup now works
  when at-rest encryption is enabled (it previously failed), retention
  safety-backups run on encrypted installs, and restoring a plaintext backup on
  an encrypted install no longer leaves the app unable to start.
- **Multi-user visibility** - a savings goal's balance can no longer leak to a
  household member who cannot see its linked account; applying an AI category or
  creating a transaction from a receipt now respects account visibility; and the
  cloud AI batch re-checks the never-cloud and privacy-mode rules at send time.
- **Hardening** - the stored auto-unlock key file is created with strict
  permissions from the outset, a crafted receipt can no longer tie up OCR, the
  image-extract endpoints are per-minute rate-limited, and a short `HAFI_DB_KEY`
  no longer breaks saving an AI key or enrolling two-factor.
- **Data integrity** - merging categories or vendors now keeps subscriptions and
  rules pointed at the surviving record instead of silently dropping the link.
- **Correctness** - the savings page no longer errors on a slow-progress goal; a
  linked goal in a different currency to its account is converted; the live
  energy offset respects a cumulative (lifetime-total) production sensor and its
  history is split-aware; vendor default categories apply from the linked
  vendor; re-teaching a description to a new category now takes effect; and
  several endpoints return a clear "unknown category" message instead of a
  server error.
- **Performance** - fewer repeated queries on the dashboard, budgets, the
  receipts list and re-categorise; receipt uploads are size-capped before
  buffering.
- **Usability** - the locked screen shows the real unlock error, more actions
  surface a message if they fail instead of doing nothing silently, and the
  in-app prompt field and split editor are more accessible.

### Upgrade notes
- Four new database migrations run automatically on start: a case-insensitive
  unique index on tags, an import-profile date-format column, MFA backup codes,
  and a per-user nav-layout column.
- If at-rest encryption is enabled (`HAFI_DB_KEY` set), you may be asked to
  **verify two-factor once** after upgrading: the TOTP secret is re-wrapped
  with application-layer encryption.
- Upgrading from **v1.0.1 or older** also brings everything in v1.0.2 below,
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
