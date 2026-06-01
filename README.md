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
  keyword); correct one transaction and optionally turn it into a **rule**.
- **Split** a transaction across several categories/projects; the dashboard uses
  the split parts.
- **Projects & tags** — collect spend toward a goal (renovation, holiday, car)
  with per-project totals and breakdowns; flexible tags on transactions.
- **Budgets** — per-category, per-project or total budgets over weekly →
  yearly periods, with on-track / near-limit / over status.
- **Subscriptions** — recurring payments detected automatically, with a monthly
  cost total.
- **Receipts** — upload a photo/PDF; optional local OCR (Tesseract) reads the
  merchant/date/total, or enter them by hand, then match to a transaction
  (amount/date/vendor scoring). OCR runs in the add-on; the rest works anywhere.
- **Review queue** — a safety net listing anything uncertain (unmatched receipt,
  low-confidence read, …) to resolve or ignore.
- **AI assistant (opt-in)** — off by default; when enabled, suggests a category
  for a transaction via any OpenAI-compatible LLM (local Ollama/LM Studio or
  cloud). It only *suggests* — you confirm. With a local LLM you can also
  **batch-categorise** uncategorised transactions and bulk-approve the
  suggestions. Cloud payloads are minimised and redacted; in cloud-manual mode
  you preview and approve (or reject) each request; categories you mark
  *never-cloud* are never sent; and every call is audited.
- **Multi-currency** — original amount kept and converted to your base currency;
  manual rates by default, opt-in online ECB rates (Frankfurter).
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

Then open the **Dashboard** (pick month *2026-05*) and **Transactions**.

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
parsers: Curve, Barclays, Lloyds, Monzo, a generic CSV mapper, and a generic
**PDF** reader. CSV is most reliable; PDF statements are read best-effort and
each extracted row is flagged for review so you can verify it. Duplicate rows
(and re-uploaded files) are detected and skipped
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

