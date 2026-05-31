# HA Finance Intelligence

A **local-first, Home Assistant-first personal finance app**. Import bank
statements, categorise transactions, build a vendor/category library, track
spending, and (later) scan receipts and use optional local/cloud AI — all
privacy-first, with **strict local mode as the default**.

Full design: [`ha_finance_intelligence_spec.md`](ha_finance_intelligence_spec.md)
(the build-status section at the top tracks progress).

## Status

| Stage | What | State |
|-------|------|-------|
| 0 | Project skeleton (FastAPI + SQLite + React + add-on) | ✅ |
| 1 | CSV import (Curve/Barclays/Lloyds/Monzo/generic), dedup, transactions | ✅ |
| 2 | Categories, vendors, auto-categorisation, dashboard | ✅ |
| — | Data-safety pass: redaction, backup/restore, demo data, security hardening | ✅ |
| 3+ | Rules, splits, projects, budgets, review queue, receipts, AI | planned ([spec §29](ha_finance_intelligence_spec.md)) |

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
  ├── CSV import + parser engine
  ├── category / vendor / rule engine
  └── (later) MQTT publisher, OCR, AI gateway
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

## Importing your own statements

Use **Import**, pick your bank (or auto-detect), preview, then confirm. Built-in
parsers: Curve, Barclays, Lloyds, Monzo, plus a generic mapper for anything
else. Duplicate rows (and re-uploaded files) are detected and skipped
([spec §14](ha_finance_intelligence_spec.md)). Sample fake CSVs live in
[`examples/sample-csv/`](examples/sample-csv/).

## Privacy, security & backups

- **Privacy model:** [docs/privacy.md](docs/privacy.md) — local-first, what
  happens when AI is enabled, and what we can/can't guarantee about third-party
  AI providers.
- **Security & isolation:** [docs/security.md](docs/security.md) — the database
  lives in the add-on's private `/data` volume; file permissions, AppArmor, and
  the honest limits of isolation inside Home Assistant.
- **Backup/restore:** Settings page — download/restore the database and
  export/import your config + library as JSON. Encrypted/cloud backup is on the
  roadmap (backlog #15).

## Home Assistant add-on

The [`addon/`](addon/) folder contains everything to run this as a local add-on
(ingress sidebar panel on port 8099, private `/data` storage). Add-on
repository packaging and install docs land in Stage 12
([spec §29](ha_finance_intelligence_spec.md)).

## License

Licensed under the [Apache License 2.0](LICENSE).
