# Privacy model

HA Finance Intelligence handles some of the most sensitive data a household
has: bank transactions, salaries, mortgage and medical payments, and (later)
receipt images. This document explains exactly what the app does and does not
do with that data, and is the honest answer to the privacy questions in the
project backlog.

See also the spec: [§7 Privacy Modes](../ha_finance_intelligence_spec.md),
[§22 AI Gateway](../ha_finance_intelligence_spec.md),
[§28 Security Requirements](../ha_finance_intelligence_spec.md).

## 1. The core guarantee: local-first

The strongest privacy protection is architectural, not a feature you switch on:

- **No external network calls by default.** Strict Local Mode is the default
  (`privacy_mode: strict_local`). In this mode the app makes **zero** outbound
  requests — no AI, no telemetry, no vendor lookups, no exchange-rate fetches.
- **All data stays inside your Home Assistant.** The database is a local SQLite
  file under `/config/finance/`.
- **No accounts, no SaaS.** The app never phones home.

If you never enable AI, **none of your financial data ever leaves your
device.** Everything below only becomes relevant when you explicitly turn on a
cloud AI mode.

## 2. When you enable cloud AI

Cloud AI is opt-in and is layered behind several safeguards (built incrementally
across [spec §22](../ha_finance_intelligence_spec.md) / Stage 10):

| Safeguard | What it means | Status |
|-----------|---------------|--------|
| Minimal payload | We send one transaction at a time, only `description`, `amount`, `currency`, and candidate category names — nothing else. | `redact_for_cloud()` implemented |
| Redaction | Card/account/IBAN/sort-code/postcode/email tokens are stripped before sending. | `redaction.py` implemented + tested |
| Never-cloud categories | Transactions in sensitive categories (salary, mortgage, medical, legal, tax, insurance, loans) are **never** sent externally. | category flag exists; enforcement in Stage 10 |
| Manual approval | In `cloud_manual` mode you see the exact payload and approve each request. | Stage 10 |
| Full audit log | Every external request (provider, model, redacted payload, response) is logged. | `ai_requests` table exists; wiring in Stage 10 |

### What we can and cannot guarantee about the AI provider

Being honest about the boundary of what software can enforce:

**We *can* guarantee (in code):**
- The AI only ever receives the minimal, redacted payload above.
- The AI is **stateless from our side** — it has no connection back to your
  database. It cannot "read" anything except the single request we send it. It
  cannot browse, query, or retain access to your data.
- Sensitive categories and raw statements/receipt images are never sent unless
  you explicitly override the defaults.

**We *cannot* technically force a third-party provider to:**
- Delete your prompt after processing, or
- Refrain from training on it.

  Those are **provider-policy** matters, not something any client app can
  enforce. The mitigation is to **choose a zero-retention / no-training
  endpoint** (most providers offer an enterprise or "no data retention" mode)
  and to prefer a **local LLM** (Ollama / LM Studio) where the data never leaves
  your network at all. The app will surface a disclaimer and let you record
  which provider/mode you've chosen (Stage 9–10).

**Recommendation:** if privacy is paramount, use **Local LLM Mode** — you get
AI assistance with the same local-only guarantee as Strict Local Mode.

## 3. Redaction details

`backend/app/services/redaction.py` masks, before any cloud call:

- Card / PAN numbers (13–19 digits) → `[card] 1234` (last 4 kept)
- UK sort codes (`12-34-56`) → `[sort-code]`
- Account numbers (8-digit runs) → `[account]`
- IBANs → `[iban]`
- UK postcodes → `[postcode]`
- Email addresses → `[email]`

It is a pure function with no DB/network access, unit-tested in
`backend/app/tests/test_redaction.py`.

## 4. Testing never touches live data

The test suite forces an isolated temporary database and refuses to run against
anything else (`backend/app/tests/conftest.py`, verified by
`test_isolation.py`). Running the tests can never read or modify your real
finance database (backlog #30).

## 5. What about other services on your Home Assistant?

See [docs/security.md](security.md) for how the database file is protected and
the limits of isolation within Home Assistant's add-on model.
