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
- **All data stays on your device.** The database is a local SQLite file in the
  add-on's **private `/data`** volume (not the shared HA `/config`), or — running
  standalone — the `finance_data` Docker volume. See [security.md](security.md).
- **No accounts, no SaaS.** The app never phones home.

If you never enable AI, **none of your financial data ever leaves your
device.** Everything below only becomes relevant when you explicitly turn on a
cloud AI mode.

## 2. When you enable cloud AI

Cloud AI is opt-in and is layered behind several safeguards
([spec §22](../ha_finance_intelligence_spec.md)) — **all of these now ship**:

| Safeguard | What it means | Status |
|-----------|---------------|--------|
| Minimal payload | We send one transaction at a time, only `description`, `amount`, `currency`, and candidate category names — nothing else. | ✅ `redact_for_cloud()` |
| Redaction | Card/account/IBAN/sort-code/postcode/email tokens are stripped before sending. | ✅ `redaction.py` (unit-tested) |
| Never-cloud categories | Transactions in sensitive categories (salary, mortgage, medical, legal, tax, insurance, loans) are **never** sent externally. | ✅ enforced in `ai_service` |
| Manual approval | In `cloud_manual` mode you see the exact payload and approve each request; the cloud **batch** flow previews the whole redacted list before you approve it in one go. | ✅ per-call + batch |
| One-time disclaimer | The first time you select a cloud mode, a dialog spells out what it means and the choice is gated until you confirm. | ✅ |
| Full audit log | Every external request (provider, model, redacted payload, response) is logged and viewable. | ✅ `ai_requests` + Logs page |

**Receipts never go to AI.** Only a transaction's `description` (redacted) is ever
sent — receipt images and OCR text are never included in an AI payload. The
**re-process** action (re-run AI over already-categorised rows to find better
matches) uses the same gated, redacted, suggestion-only path and never overwrites
a manual choice.

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
  your network at all. The app surfaces a one-time disclaimer when you choose a
  cloud mode and records the provider/mode you've configured.

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

## 6. Requesting deletion / zero-retention from AI providers

Two questions come up a lot, and the honest answer is the same for both: **a
client app cannot force a third party to delete or not-retain data.** What the
app *does* do is keep this in your control:

- **It's opt-in and logged.** Cloud AI is off by default. When you do enable it,
  every external send is recorded in **Logs → Decisions** (and the AI-requests
  log) with the date, who, and what was sent — so you always have a record of
  exactly what a provider received and could be asked to delete.
- **Prefer not sending in the first place.** A **local LLM** (Ollama / LM Studio)
  keeps everything on your network — there is nothing to retain or delete. This
  is the recommended setup if retention worries you at all.

If you do use a cloud provider, here's how retention/deletion works in practice.
**Provider terms change — always verify the current policy at the links below.**

| Provider | Training on your data? | Retention / zero-retention | Deletion |
|----------|------------------------|----------------------------|----------|
| **Local LLM** (Ollama / LM Studio) | Never — runs on your hardware | Nothing leaves your network | N/A — there is nothing stored externally |
| **OpenAI API** | Not used to train models by default for API traffic | Inputs/outputs held for a short abuse-monitoring window, then deleted; **Zero Data Retention** is available for eligible accounts (nothing is stored) | Request via your account / support; ZDR avoids storage entirely ([platform.openai.com/docs](https://platform.openai.com/docs/guides/your-data)) |
| **Anthropic API** | Not used to train by default for API traffic | Held per the commercial data-retention policy | Request via support / your account ([anthropic.com/legal](https://www.anthropic.com/legal/commercial-terms)) |
| **Azure OpenAI** | Not used to train | Optional **no content logging**; data stays in your tenant/region | Managed through your Azure subscription ([learn.microsoft.com](https://learn.microsoft.com/azure/ai-services/openai/concepts/data-privacy)) |
| Other OpenAI-compatible endpoint | Depends entirely on the operator | Read their policy before enabling | Per their process |

**Images (receipt/statement vision extraction).** When you use the opt-in
image-AI fallback, the **image itself** is sent and **cannot be redacted** (the
app warns you each time until you accept the risk). Receipts usually already have
card numbers masked, but treat any scan as sensitive: prefer a **local** vision
model, or a cloud provider with **zero data retention**, and use the Decisions
log to track what was sent if you later want to request its deletion.

**Bottom line:** the app gives you the record and the controls (off by default,
local option, per-send logging, redaction for text); enforcing deletion is the
provider's job, and choosing zero-retention or local is how you avoid needing it.
