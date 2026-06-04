# Changelog

All notable changes to HA Finance Intelligence. This project uses date-stamped,
human-readable entries; versions follow semantic-ish versioning while pre-1.0.

## v0.9.5-beta — 2026-06-04

The practical **close of beta development**. Everything the first beta listed as
"not in this release" has since landed, plus a wave of depth and polish. Still
standalone-first; the **Home Assistant add-on packaging is the next release**
(and the point at which the branch strategy switches — `main` becomes the HA
release line).

> Same beta caveat: provided "as is", no warranty, not financial advice — keep
> your own backups.

### Added
- **Investments & pensions** — distinct models: an *investment* account is
  **holdings-first** (tickers × price → market value, unrealised gain, value-over-
  time chart with day/month/year change) while a *pension* tracks a **statement
  value** with contributions/withdrawals. Optional, off-by-default **price feed**
  (keyless Stooq or keyed Alpha Vantage; only ticker symbols leave the box).
- **Cars, home & assets** — a car uses one consistent unit system (imperial MPG or
  metric L/100km) with refuel/economy history; a home tracks utility-meter readings
  → usage & cost; plus maintenance/running-cost logs.
- **Spending by location** — a world/geo map ranks the month's spend by country
  (per-transaction country → vendor country → currency fallback), with a
  `set_country` **rule action** and per-trip/per-vendor country overrides.
- **Paperless-ngx import** — pull documents from your own Paperless into receipts
  (outbound-only; off until a URL + env token are set).
- **Over-time charts** with a 6M/1Y/2Y/5Y range selector across Investments,
  Savings, Travel and Projects; Business gets a year scope.
- **"Needs attention"** dashboard card (review queue + uncategorised + FX-needed)
  and a Review-page **Uncategorised** tab with inline quick-categorise.
- **AI re-process** — re-run the model over already-categorised rows to find better
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

## v0.9.0-beta — 2026-06-07

First public **beta**. A complete, privacy-first personal-finance app you can run
**standalone** (Docker) today. Home Assistant integration (one-click add-on
install, MQTT sensors, ingress SSO) is scaffolded but **not part of this release** —
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

- **Import** bank/card statements (CSV, OFX/QIF, XLSX, PDF — incl. scanned-PDF
  OCR), with duplicate detection and an "already imported" guard.
- **Categorise** automatically (rules → vendor → keyword) with an in-app rules
  guide; **split** transactions; **projects**, **tags**, **budgets** (weekly→yearly).
- **Dashboard**: customisable, reorderable cards — spend/income/net, trends,
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
