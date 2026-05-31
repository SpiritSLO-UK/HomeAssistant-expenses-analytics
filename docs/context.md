# Project context & decision log

A durable, committed record of how HA Finance Intelligence is built and **why**,
so nothing important gets lost between sessions or contributors (backlog #80).
This complements:

- [`ha_finance_intelligence_spec.md`](../ha_finance_intelligence_spec.md) — the full product/architecture spec (see §0 Build Status).
- [`README.md`](../README.md) — how to run/develop/test.
- [`docs/privacy.md`](privacy.md) / [`docs/security.md`](security.md) — privacy & isolation model.
- `things-to-add-change-consider.md` — the owner's backlog (kept local/untracked).

> Keep this file current when a major decision is made or a stage lands.

## What this is

A local-first, Home-Assistant-first personal finance app. Import bank
statements → categorise → analyse, privacy-first, no cloud required. Optional
local/cloud AI comes later and is strictly opt-in.

## Architecture at a glance

```
addon/      Home Assistant add-on (single container): config.yaml, Dockerfile,
            run.sh, apparmor.txt.example
backend/    FastAPI (Python 3.12) + SQLAlchemy 2.0 + Alembic + SQLite
  app/api/        routers, one per resource, aggregated in api/router.py (prefix /api)
  app/models/     SQLAlchemy models (one file per entity)
  app/services/   business logic (import, category, vendor, dashboard, fx,
                  settings, backup, demo, redaction, household)
  app/parsers/    bank CSV parsers + detection registry
  app/schemas/    Pydantic request/response models
  app/tests/      pytest (forced temp DB)
frontend/   React + TypeScript + Vite (TanStack Query); served by the backend at /
docs/, examples/, scripts/
```

Request flow: React → `/api/*` (FastAPI) → service → SQLAlchemy → SQLite. The
HA integration holds no business logic (spec §9.4).

## Key decisions (and why)

| Decision | Why |
|----------|-----|
| **License: Apache-2.0** | Max adoption over AGPL (owner's call, 2026-05-31). |
| **SQLite for MVP** | Simple inside an add-on; Postgres later if needed (spec §9.2). |
| **DB in the add-on's private `/data`** (not `/config`) | Isolation: other add-ons / the HA config share can't read it, and we can't read HA secrets. Deviation from spec §26.4 — see docs/security.md. |
| **Enums stored as `String` columns** | Portability across SQLite/Postgres; values documented in the model. |
| **Frontend: relative base + HashRouter** | Works under any HA ingress path without knowing it. |
| **Signed amounts** (negative = debit/out) | One convention everywhere (spec §13). |
| **Source-hash dedup** `sha256(account\|date\|amount\|currency\|desc\|posted)` | Idempotent imports; re-uploads don't duplicate (spec §14.5). |
| **Keyword match is word-boundary** (`\b`+prefix) | Fixed "tfl" matching inside "neTFLix"; prefixes (sainsbury→sainsburys) still match. |
| **FX: manual by default, Frankfurter opt-in** | Strict-local stays call-free; online FX is a conscious opt-in. Store original + base_amount; never rewrite an existing rate; backfill missing only (owner's rule). |
| **Tests force a temp DB** | A test run can never read/modify real data (backlog #30). |

## Conventions & gotchas

- **Never commit** `things-to-add-change-consider.md` (owner's backlog; excluded via `.git/info/exclude`). Update it with status + spec section after each major step.
- Reference spec sections as clickable links in all comms/docs (backlog #20).
- `sqlite3.connect(...)` in a `with` block commits but does **not close** — close explicitly or the file stays locked on Windows (bit us in backup snapshot).
- Hit the running server at `http://127.0.0.1:8099`, not `localhost` (IPv6 `::1` vs IPv4 bind).
- Run everything via `scripts/test.sh` (pytest + tsc) and `scripts/dev.sh`.

## Build status (summary)

Stages 0–3 done (skeleton, CSV import, categories/vendors + dashboard, rules &
learning), plus a data-safety pass (redaction, backup/restore, demo data, add-on
isolation) and multi-currency. Full detail in spec §0 and git history.

Categorisation order (spec §15.1): **manual > rule > vendor default > keyword**.
Rules (`rule_service`) run on import and re-categorise; a manually-set category
(confidence 1.0) is never overridden. "Make rule" / `learn_rule` turns a
correction into a high-priority description rule.

## Decided but not yet built

- **#15 Encryption / cloud backup** — optional SQLCipher at-rest (user chooses a
  key or not); **both** unlock modes (UI prompt *and* stored key); **local**
  encrypted backups now, cloud later. Lost key = unrecoverable data.

## Open questions / to scope

- **#78 Data retention/expiry** — purge vs archive? which data (transactions,
  receipts, AI logs)? per-category retention?
- **#79 Multi-user roles** — admin / member / viewer / child; individual vs
  group/shared views; per-account permissions.
- **#29 FX coverage** — Frankfurter is ECB (~30 currencies); add a wider source
  (e.g. fawazahmed0 API) if exotic currencies are needed.

## Working agreements

- Strict-local by default; anything that leaves the device is opt-in and audited.
- Ask the owner when a decision is genuinely theirs; otherwise pick a sensible
  default and say so.
- Verify changes (tests + a live run) before claiming done.
