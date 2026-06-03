# HA Finance Intelligence

[![CI](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/actions/workflows/ci.yml/badge.svg)](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/actions/workflows/ci.yml)

A **local-first, Home Assistant-first personal finance app**. Import bank
statements, categorise transactions (rules + a vendor/category library), split
them across categories, track projects, budgets and subscriptions, scan receipts
(local OCR), handle multiple currencies, and publish finance sensors to Home
Assistant over MQTT — with **optional, opt-in** local/cloud AI to suggest
categories. All privacy-first, with **strict local mode as the default**.

Full design: [`ha_finance_intelligence_spec.md`](ha_finance_intelligence_spec.md)
(the build-status section at the top tracks progress).

## 🧪 Beta — run it standalone (no Home Assistant needed)

This is the **v0.9.0-beta** release: a complete, standalone app you can run today
with Docker. The Home Assistant add-on (one-click install, ingress SSO, MQTT
sensors) is scaffolded under [`addon/`](addon/) but ships in a **later** release.

```bash
git clone https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics.git
cd HomeAssistant-expenses-analytics
docker compose up -d --build      # build + start
# open http://localhost:8099
```

Your data (SQLite DB + uploads + safety backups) is kept in the `finance_data`
Docker volume. Standalone, the app runs single-user as a local owner. Set your
base currency and other options in [`docker-compose.yml`](docker-compose.yml) (or
`HAFI_*` env vars), then **Settings → Demo data → Load demo data** to explore.
See the [CHANGELOG](CHANGELOG.md) for what's in this beta. _Beta software: no
warranty, not financial advice — keep your own backups._

## Status

| Stage | What | State |
|-------|------|-------|
| 0 | Project skeleton (FastAPI + SQLite + React + add-on) | ✅ |
| 1 | CSV import (Curve/Barclays/Lloyds/Monzo/generic), dedup, transactions | ✅ |
| 2 | Categories, vendors, auto-categorisation, dashboard | ✅ |
| 3 | Rules & learning (a correction can become a rule) | ✅ |
| 4 | Split transactions (one charge across several categories) | ✅ |
| 5 | Projects & tags | ✅ |
| 6 | Budgets + alerts, and MQTT sensors published to Home Assistant | ✅ |
| 7 | Review queue (resolve/ignore the things the app is unsure about) | ✅ |
| 8 | Receipts & OCR (upload, optional local OCR, match to a transaction) | ✅ |
| 9 | Local AI (opt-in): category suggestions via any OpenAI-compatible LLM | ✅ |
| — | Recurring payments & subscriptions (auto-detected) | ✅ |
| — | Data-safety: redaction, backup/restore, demo data, security hardening | ✅ |
| — | Multi-currency + FX; encrypted backups + optional at-rest encryption | ✅ |
| 10 | Cloud AI approval: preview + approve/reject each request, never-cloud category blocking, audit log | ✅ |
| 11 | PDF statement import: best-effort, rows flagged for review | ✅ |
| 12 | Polish (CI, dashboard trends/outliers, savings, logs, security/multi-user, …) | planned ([spec §29](ha_finance_intelligence_spec.md)) |

## What it does today

- **Import** bank statements (Curve, Barclays, Lloyds, Monzo, or a generic CSV
  mapper) with duplicate detection on re-upload. PDF statements import
  best-effort, with each extracted row flagged for review.
- **Categorise** automatically (priority order: manual > rule > vendor default >
  keyword); correct one transaction and optionally turn it into a **rule**. The
  Rules page has a built-in **"How rules work"** guide explaining every condition
  and action with worked examples.
- **Manage categories** — add your own, recolour or rename any category, set its
  cloud-AI privacy level, **delete** one (its transactions fall back to
  uncategorised) or **merge** one into another. This now includes the built-in
  library categories; deleted built-ins can be restored with "Import library".
  Set the **cloud-AI privacy level for every category at once** (it also becomes
  the default new categories inherit), with per-category fine-tuning behind an
  "Advanced" toggle.
- **Split** a transaction across several categories/projects; the dashboard uses
  the split parts.
- **Projects & tags** — collect spend toward a goal (renovation, holiday, car)
  with per-project totals and breakdowns; flexible tags on transactions.
- **Budgets** — per-category, per-project or total budgets over weekly →
  yearly periods, with on-track / near-limit / over status.
- **Savings** — track savings-account balances over time (manual snapshots, with
  a growth sparkline) and set **goals** with progress bars; a goal can follow a
  savings account's latest balance or be tracked by hand.
- **Investments & pensions** — track investment platforms and pensions: record a
  **value** from a statement (best for pensions, with a growth sparkline) or add
  **holdings** (units of a ticker with an average cost and a last price) to see
  market value and unrealised gain (±£ and %). An **optional price feed** keeps
  holding prices current — **off by default**, with a choice of source: keyless
  public quotes (Stooq) or a keyed provider (Alpha Vantage via
  `HAFI_INVESTMENT_API_KEY`). Only the ticker symbol is ever sent — never your
  balances or holdings — and you can always just enter prices by hand.
- **Cars & assets** — track a car, your home or anything else with a log timeline.
  A car's **refuel** entries (odometer + litres + cost) yield **MPG** (imperial
  gallon) and **L/100km** between full fills, plus servicing/running costs and a
  per-fill economy history. Odometer is in your unit (miles by default, or km).
  A **home** tracks **utility meter readings** (electricity/gas/water) → usage and
  cost between readings, plus maintenance/running costs.
- **Subscriptions** — recurring payments detected automatically, with a monthly
  cost total, plus **alerts** for renewals due soon and payments that look
  missed (also surfaced in the dashboard heads-up).
- **Receipts** — upload a photo/PDF; optional local OCR (Tesseract) reads the
  merchant/date/total, or enter them by hand, then match to a transaction
  (amount/date/vendor scoring). OCR runs in the add-on; the rest works anywhere.
  You can also **attach a receipt directly to a transaction** from its drill-down
  detail and **view the image/PDF** there — attached receipts keep their original
  (so they stay viewable) regardless of the delete-after-processing setting.
- **Review queue** — a safety net listing anything uncertain (unmatched receipt,
  low-confidence read, …) to resolve or ignore.
- **AI assistant (opt-in)** — off by default; when enabled, suggests a category
  for a transaction via any OpenAI-compatible LLM (local Ollama/LM Studio or
  cloud). It only *suggests* — you confirm. You can **batch-categorise**
  uncategorised transactions either with a **local LLM** (on-device; scan →
  bulk-approve) or with **cloud AI** (review the exact redacted payloads that
  would be sent → approve the whole list at once → review the returned
  suggestions → apply). Cloud payloads are minimised and redacted; in
  cloud-manual mode you preview and approve (or reject) each request; categories
  you mark *never-cloud* are never sent; and every call is audited. The first
  time you enable a cloud mode, a one-time disclaimer spells out exactly what
  this means.
- **Services panel** — a **Settings → Services** card to see and switch each
  service from one place: the **AI assistant** (a status + "turn off" — it reads
  *On* only when a real local/cloud mode is configured, *Off* otherwise), receipt
  **OCR** on/off, and **online exchange rates** on/off. MQTT is shown read-only
  (it's configured in the add-on options). Owner/settings-manager only.
- **Multi-currency** — original amount kept and converted to your base currency;
  manual rates by default, opt-in online ECB rates (Frankfurter).
- **Multi-user & roles** — identity comes from Home Assistant (the first person
  becomes the **owner/administrator**); anyone new appears **pending** and has no
  access until the owner approves them. Roles are *owner* (admin), *member*
  (read/write), *viewer* (read-only), and *child* (allowance-only). Read-only
  roles can't change anything, and the last owner can't be removed. (Standalone,
  with no HA in front, it runs single-user exactly as before.) The **general
  Settings and nav-tab customisation are owner-only** by default; the owner can
  grant any member a **"manage settings"** permission from the Users page (each
  user still manages their own two-factor security). A **Setup wizard** (from the
  Users page) branches by household shape — **Solo** (set currency + import) or
  **Household / Family** (approve people + roles, share/privatise accounts, give
  kids an allowance).
- **Shared vs private accounts** — on the **Accounts** page, mark an account
  *private* and it (and its transactions) drop off everyone else's dashboards,
  budgets, exports and lists — only you and the household owner see it. Accounts
  stay *shared* by default. A **Mine / Shared / All** toggle on the dashboard lets
  you switch between your own, the household's shared, and everything you can see.
- **Kids' allowance** — the *child* role is a friendly pocket-money view: a child
  sees only **their** budgets (candy, toys…), **their** savings, and an itemized
  list of purchases attributed to them. Parents attribute spend from the
  Transactions page (a whole purchase or just part) or add manual items — and it
  shows on the kid **without changing the parent's own expenses or budgets**.
- **Two-factor (optional)** — each user can turn on TOTP MFA (Google
  Authenticator, Aegis, 1Password…): a 6-digit code to open the app, and a fresh
  code to confirm admin actions. Time-based, on-device, off by default.
- **Security health** — an owner-only panel flags protections that are off
  (no at-rest encryption, no MFA, repeated failed unlock attempts, …) with a
  one-line fix for each. It never nags: dismiss or snooze any item.
- **Logs / activity** — an owner-only **Logs** page shows an activity log of
  important actions (statement import & delete, transaction delete, demo-data
  load, user role/approval changes, MFA enable/disable — filterable by action)
  plus the AI-call log. Low-level runtime/debug logs stream to the Home Assistant
  add-on **Log** panel at your chosen `log_level`.
- **Data retention** — owner-only, off by default. For each kind of data
  (transactions, AI request logs, activity/audit logs, receipt files,
  failed-unlock records) you can **archive after N days** (reversible — hidden
  from lists *and* every total, kept) and/or **purge after N days**
  (permanent). A dry-run **removal plan** shows exactly what would go; purging is
  confirm-only (with a fresh MFA code if you use MFA) unless you opt a type into
  **auto-purge**, and a **timestamped safety backup** is taken before any purge
  (the backup history is trimmed by age/size). Receipts also have a default-on
  "delete the original file once it's processed & matched" toggle that keeps the
  extracted fields but drops the image. Changing the policy or running a purge is
  owner + MFA-gated.
- **CSV export** — download your transactions (the "Export CSV" button on the
  Transactions page honours the active filters and exports the whole filtered
  set), plus the data behind the dashboard charts (spending-by-category and the
  monthly spend/income/net trend) from small "⬇ CSV" links. Files carry a UTF-8
  BOM so they open cleanly in Excel.
- **Resizable columns** — drag the edge of any column header on the Transactions
  table to set its width; the widths are remembered on your device ("↔ Reset
  columns" restores the defaults).
- **Global search** — a **Search** page finds any transaction (by description,
  merchant or amount), vendor, category or project and links straight to it.
  Transaction results are scoped to what you're allowed to see.
- **Customisable dashboard** — a **⚙ Customise** toggle lets you show/hide **and
  reorder** the optional cards (Heads-up, Trends, Spending by category, Top vendors,
  By project, By member, Savings, Budgets, Business, Travel, Allowance, Processing)
  with up/down arrows; the layout and order are remembered on your device.
- **Spending by member** — for multi-person households, a card breaking the
  month's spend down per member (plus a "Shared" row for joint accounts). Each
  person's figure covers the accounts they own, scoped to what you're allowed to
  see — so it never exposes another member's private spend.
- **Domain summary cards** — compact per-area cards (Savings, Budgets, Business,
  Travel, Allowance) appear on the dashboard only when that area has data, each
  toggleable and linking through to its full page — so the dashboard reflects
  exactly the features you actually use.
- **Processing card** — a pipeline-status snapshot: statements and transactions
  imported, receipt OCR progress, and how many enrichment calls went through AI
  (cloud vs local) with the average AI turnaround — so you can see at a glance how
  much was handled locally vs sent to a cloud model (AI is off by default).
- **Trends & heads-up** — the dashboard shows month-over-month spend/income/net
  sparklines with up/down arrows, and a non-nagging "heads-up" card that flags
  unusually large charges, categories spending well above their recent average,
  brand-new merchants, and budgets near or over. Heuristics are conservative and
  only kick in once there's enough history.
- **Travel / spend-abroad** — a **Travel** page groups foreign-currency spend by
  currency (with a friendly country label) and **auto-detects trips** from clusters
  of foreign spend; turn any trip into a project (with a budget) in one click.
- **Business expenses / VAT** — flag a transaction as **business** (per-row toggle
  + a "business only" filter), capture its **VAT** (by hand or auto-filled from a
  matched receipt), and a **Business** page totals business spend + reclaimable VAT
  by category & month with a CSV export for claiming.
- **Home Assistant sensors** — optional MQTT discovery publishes spend/income/net,
  review count, per-budget progress, per-project totals and monthly subscriptions.
- **Privacy & safety** — strict local by default, redaction, backup/restore,
  encrypted backups, optional at-rest encryption, and tests that never touch live
  data.

## Design principles

Home Assistant first · strict local by default · CSV before PDF · rules before
AI · user correction beats AI · the bank transaction is the source of truth ·
AI is an assistant, not an authority · everything uncertain goes to review ·
everything external is auditable. ([spec §43](ha_finance_intelligence_spec.md))

## Can I run it without Home Assistant?

**Yes — fully.** The backend is a normal FastAPI app and the frontend a normal
Vite app. You only need Home Assistant to run it *as an add-on* with the
sidebar panel. For development, testing and trying it out, you don't need HA at
all (see Quick start). Everything the UI does goes through the REST API under
`/api`, so the app is fully API-driven and scriptable — interactive API docs are
served at **`/docs`** when the backend is running.

## Architecture (MVP)

```
Home Assistant add-on (single container)
  ├── FastAPI backend (Python 3.12)        — API under /api
  ├── React + TypeScript frontend (Vite)    — served at /
  ├── SQLite database (SQLAlchemy + Alembic)
  ├── CSV + PDF import + parser engine
  ├── category / vendor / rule engine
  ├── splits · projects/tags · budgets · subscription detection
  ├── multi-currency (FX) · optional at-rest encryption (SQLCipher)
  ├── receipts + optional local OCR (Tesseract/pypdf) · review queue
  ├── MQTT publisher → Home Assistant sensors
  ├── AI gateway (optional, opt-in: local/cloud OpenAI-compatible LLM)
  └── (later) cloud-approval UI, PDF import
```

## Repository layout

```
backend/    FastAPI app, models, services, parsers, migrations, tests
frontend/   React + TypeScript + Vite UI
addon/      Home Assistant add-on (config.yaml, Dockerfile, run.sh, apparmor)
docs/       privacy.md, security.md, …
examples/   Sample (fake) bank CSVs
scripts/    test.sh / dev.sh (bash; Linux/macOS/WSL + Git Bash)
```

## Requirements & recommended hardware

It's deliberately light — SQLite + FastAPI, no heavy services. As a Home Assistant
add-on it runs comfortably on a **Raspberry Pi 4 (or 5)** or any HA host (x86/ARM);
a Pi 3 works but will feel slower on imports/OCR.

- **CPU/RAM:** modest — a few hundred MB; the app itself is I/O-light.
- **Disk:** small (your statements + SQLite DB + optional safety backups); receipt
  images are the main consumer if you store them.
- **Optional extras add load:** OCR (Tesseract) and PDF rasterising are CPU-spikey
  on a Pi during processing; at-rest **encryption** (SQLCipher) adds a little CPU.
  AI is off by default and, when enabled, can point at a local LLM or a cloud
  endpoint (your choice).

## Quick start (local, no Home Assistant)

Prerequisites: **Python 3.12+** and **Node.js 20+**.

### 1. Backend

```bash
python3 -m venv backend/.venv
# Linux/macOS/WSL:   source backend/.venv/bin/activate
# Windows PowerShell: backend\.venv\Scripts\Activate.ps1
backend/.venv/bin/python -m pip install -e 'backend[dev]'

cd backend
alembic upgrade head          # create the SQLite database
python -m app.main            # serves on http://localhost:8099
```

Open <http://localhost:8099/api/health> → `{"status":"ok", ...}`, and
<http://localhost:8099/docs> for the API explorer.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173 (proxies /api to :8099)
npm run build                 # outputs frontend/dist (served by the add-on)
```

### 3. Or use the helper scripts

```bash
./scripts/dev.sh              # starts backend + frontend together
./scripts/test.sh             # backend tests + frontend type-check
```

### 4. Load demo data

In the UI go to **Settings → Load demo data**, or:

```bash
curl -X POST http://localhost:8099/api/backup/demo
```

The demo set is generated **relative to today** — it spans the current month plus
the previous two, across many vendors and categories, and includes two
foreign-currency **trips** (so the **Travel** page populates) and a few
**business** transactions with **VAT** (so the **Business** page shows reclaimable
VAT). Open the **Dashboard**, then explore **Transactions**, **Travel** and
**Business**; the **Trends** card shows the month-on-month shape.

Done exploring? **Settings → Remove demo data** (owner-only) deletes everything the
demo seeded — its transactions, example projects/budgets/savings, demo members,
vendors and review items — leaving a clean database. It removes **only** the
demo's own rows (tracked from when it was loaded), so any real statements you have
imported and anything you added yourself are left untouched.

## Testing

```bash
./scripts/test.sh                       # everything
cd backend && .venv/bin/python -m pytest # backend only
```

Tests run against a throwaway temporary database and **refuse to start against
a real one** — they can never read or modify your finance data
([docs/privacy.md](docs/privacy.md), backlog #30).

**CI:** [GitHub Actions](.github/workflows/ci.yml) runs `ruff` + the backend
tests and the frontend type-check/build on every push and pull request. On Linux
CI installs all optional extras, so the at-rest encryption, MQTT, OCR and PDF
paths are exercised for real.

## Importing your own statements

Use **Import**, pick your bank (or auto-detect), preview, then confirm. Built-in
parsers: Curve, Barclays, Lloyds, Monzo, a generic CSV mapper, a generic
**PDF** reader, and an **image/scan** reader. CSV is most reliable; PDF, **photos
or scans (JPG/PNG), and scanned PDFs** are read best-effort with OCR (Tesseract;
scanned PDFs are rasterised first) and each extracted row is flagged for review so
you can verify it. Duplicate rows (and re-uploaded files) are detected and skipped
([spec §14](ha_finance_intelligence_spec.md)). Sample fake CSVs live in
[`examples/sample-csv/`](examples/sample-csv/).

## Privacy, security & backups

- **Privacy model:** [docs/privacy.md](docs/privacy.md) — local-first, what
  happens when AI is enabled, and what we can/can't guarantee about third-party
  AI providers.
- **Security & isolation:** [docs/security.md](docs/security.md) — the database
  lives in the add-on's private `/data` volume; file permissions, AppArmor, and
  the honest limits of isolation inside Home Assistant.
- **Backup/restore & encryption:** Settings page — download/restore the
  database, export/import your config + library as JSON, **encrypted backups**
  (passphrase, AES-256-GCM), and optional **at-rest database encryption**
  (SQLCipher; Linux / the add-on). Cloud backup *destinations* (G-Drive/S3/
  Backblaze) are still on the roadmap (backlog #15).
- **Data retention:** Settings → Data retention (owner-only, off by default) —
  archive-then-purge windows per data type, an opt-in auto-purge, a dry-run
  removal plan, a safety backup before every purge (trimmed by age/size), and a
  default-on "delete a receipt's original once processed" toggle.

## Home Assistant add-on

The [`addon/`](addon/) folder contains everything to run this as a local add-on
(ingress sidebar panel on port 8099, private `/data` storage). Enable **MQTT** in
the add-on options to publish finance sensors (spend/income/net, review count,
per-budget progress, per-project totals, monthly subscriptions) to Home Assistant
via MQTT discovery — off by default, point it at your broker (e.g. the Mosquitto
add-on). Add-on repository packaging and one-click install docs land in Stage 12
([spec §29](ha_finance_intelligence_spec.md)).

## Disclaimer

This software is provided **"as is", without warranty of any kind** and is
**not** financial, accounting, tax, or investment advice. The authors and
contributors accept **no responsibility or liability** for any loss, damage,
inaccuracy, or corruption of data arising from its use — you use it, and store
your data with it, **at your own risk**.

We have taken reasonable, good-faith precautions to protect your data (local-first
by default, no external calls unless you opt in, private storage, redaction
before any cloud AI, backup/restore, and tests that never touch live data — see
[docs/privacy.md](docs/privacy.md) and [docs/security.md](docs/security.md)).
Even so, **you are responsible for your own backups** and for verifying that any
figures are correct before relying on them. This disclaimer is in addition to
the warranty and liability terms of the licence below.

> **Built with AI.** This project is developed with substantial help from an AI
> coding assistant (Claude). Commits carry a `Co-Authored-By` trailer noting
> this. Review the code yourself before trusting it with real data.

## License

Licensed under the [Apache License 2.0](LICENSE) (which itself disclaims
warranty and limits liability — sections 7 and 8).

