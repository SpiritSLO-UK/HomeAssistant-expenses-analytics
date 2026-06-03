# Changelog

All notable changes to HA Finance Intelligence. This project uses date-stamped,
human-readable entries; versions follow semantic-ish versioning while pre-1.0.

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
