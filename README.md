# HA Finance Intelligence

A **local-first, Home Assistant-first personal finance app**. Import bank
statements, categorise transactions, build a vendor/category library, split
transactions, track projects and budgets, and (later) scan receipts and use
optional local/cloud AI — all privacy-first, with **strict local mode as the
default**.

> Status: **Stage 0 — project skeleton.** See
> [`ha_finance_intelligence_spec.md`](ha_finance_intelligence_spec.md) for the
> full product, architecture and build specification.

## Design principles

- Home Assistant first.
- Strict local by default.
- CSV before PDF.
- Rules before AI.
- User correction beats AI.
- Bank transaction is the source of truth.
- AI is an assistant, not an authority.
- Everything uncertain goes to review.
- Everything external is auditable.

## Architecture (MVP)

```
Home Assistant Add-on
  ├── FastAPI backend (Python 3.12)
  ├── React + TypeScript frontend (Vite)
  ├── SQLite database (SQLAlchemy + Alembic)
  ├── CSV import engine
  ├── category / rule engine
  ├── MQTT publisher
  └── background job runner
```

## Repository layout

```
ha-finance-intelligence/
  backend/    FastAPI app, models, services, parsers, migrations
  frontend/   React + TypeScript + Vite UI
  addon/      Home Assistant add-on (config.yaml, Dockerfile, run.sh)
  docs/       Documentation
  examples/   Sample CSVs, category library, MQTT payloads
```

## Development

### Backend

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux/macOS:        source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head           # create / migrate the SQLite database
uvicorn app.main:app --reload --port 8099
```

Then open <http://localhost:8099/api/health> — it should return
`{"status": "ok", ...}`.

### Frontend

```bash
cd frontend
npm install
npm run dev                     # dev server (proxies /api to the backend)
npm run build                   # outputs to frontend/dist for the add-on
```

### Full dev stack

```bash
docker compose -f docker-compose.dev.yml up --build
```

## License

Licensed under the [Apache License 2.0](LICENSE).
