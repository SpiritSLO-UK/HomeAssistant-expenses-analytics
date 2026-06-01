# Project context & decision log

A durable, committed record of how HA Finance Intelligence is built and **why**,
so nothing important gets lost between sessions or contributors (backlog #80).
This complements:

- [`ha_finance_intelligence_spec.md`](../ha_finance_intelligence_spec.md) — the full product/architecture spec (see §0 Build Status).
- [`README.md`](../README.md) — how to run/develop/test.
- [`docs/architecture.md`](architecture.md) — system / flow / data-model diagrams (Mermaid).
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
- **CI** (`.github/workflows/ci.yml`): ruff + backend pytest + frontend build on
  every push/PR; Linux CI installs all extras so encryption/MQTT/OCR/PDF run for
  real. Ruff config in `backend/pyproject.toml` (line-length 120; FastAPI
  injectors whitelisted for B008; E501 ignored in tests).

## Build status (summary)

Stages 0–11 done (skeleton, CSV import, categories/vendors + dashboard, rules &
learning, split transactions, projects & tags, budgets + MQTT sensors,
recurring/subscriptions, review queue, receipts + OCR, local AI, cloud AI
approval, PDF statement import), plus a data-safety pass (redaction,
backup/restore, demo data, add-on isolation) and multi-currency. That's the full
spec §29 roadmap. **Stage 12 polish** is underway — the **security/multi-user
cluster (S1–S4) is complete**: S1 multi-user identity + RBAC + approval, S2
optional TOTP MFA + admin step-up, S3 security-health panel + failed-unlock
alerts, S4 hardening pass + docs. See "Multi-user & access control" below. Full
detail in spec §0 and git history.

> Stage-numbering note: the spec §29 order is Stage 7 = review queue, Stage 8 =
> receipts. We built recurring/subscriptions (§20, not a numbered §29 stage)
> earlier; the build-status table lists it as a "—" feature row.

Categorisation order (spec §15.1): **manual > rule > vendor default > keyword**.
Rules (`rule_service`) run on import and re-categorise; a manually-set category
(confidence 1.0) is never overridden. "Make rule" / `learn_rule` turns a
correction into a high-priority description rule.

## Splits (Stage 4 — spec §17)

`split_service` divides one transaction across categories/projects. Validation
(spec §17.2): ≥2 parts, parts sum to the transaction total **to the penny**
(integer-cents comparison), every part has a category and/or project, and all
parts share the transaction's sign. The transaction stays the source of truth
(`is_split` flag; splits cascade-delete). API: `GET /api/transactions/{id}/splits`,
`POST /…/split` (replace), `DELETE /…/split` (clear). The **dashboard category
breakdown** is split-aware (spec §37.4) — split parts contribute to their own
categories, converted with the parent's FX rate; monthly spend/income totals are
unchanged because parts sum to the whole. UI: an inline `SplitEditor` (add/remove
parts, **auto-balance** the remainder) on the Transactions page. Project-level
split reporting waits for Stage 5 (projects).

## Projects & tags (Stage 5 — spec §18)

`project_service` reports on **projects** (first-class cost collectors:
renovation, holiday, car, …). A transaction belongs to a project via its
`project_id` or — for splits — a split part's `project_id` (split transactions
are driven by their parts, never the whole). `summary(project)` gives total
spend, by-category, by-vendor, transaction count and timeline (first/last dates),
plus budget progress when `budget_amount` is set; `totals()` powers
`GET /api/dashboard/projects` (the "Project totals" card). Spend is money-out,
base-currency, split-aware — consistent with budgets. API: `/api/projects` CRUD +
`/api/projects/{id}/summary`. Assigning a transaction to a project is just
`PATCH /api/transactions/{id} {project_id}` (validated; also a `project_id` list
filter). Project budgets work through `budget_service` (project_id budgets).

`tag_service` manages **tags** (flexible labels: reimbursable, work, warranty,
…), many-to-many with transactions, names matched case-insensitively (no
"Work"/"work" dupes). API: `/api/tags` CRUD + `POST /api/transactions/{id}/tags`
(replace set, creating new names) + a `tag_id` list filter. Tags are
selectin-loaded on the transaction list (no N+1) and appear in `TransactionOut`.

UI: a **Projects** page (create, status, optional budget, status-coloured
progress bar, expandable per-project detail with by-category/by-vendor
breakdowns) and, on **Transactions**, a project `<select>` + tag chips
(click-to-remove, "+ tag" prompt) per row. Per-project total sensors are
published over MQTT (spec §27.3).

## Subscriptions / recurring payments (Stage 7 — spec §20)

`subscription_service.detect()` groups spend by vendor (or normalised merchant
name when no vendor matched) and flags groups that recur at a regular interval
with a steady amount: median gap → frequency band (weekly/monthly/quarterly/
yearly), amount deviation ≤ 35%, confidence from gap regularity + amount
consistency (+0.1 if the category name contains "subscription"). It **upserts**
(idempotent) and **never overwrites** a user's `cancelled`/`ignored` status.
Amounts/totals are base-currency (only transactions with `base_amount`). New
`Subscription` model + migration `b7c1d2e3f4a5`. Detection runs **on import
confirm** (best-effort, never breaks an import) and via `POST /api/subscriptions/detect`.
API: `/api/subscriptions` (list/detect/patch/delete) + `GET /api/dashboard/subscriptions`
(active subs + monthly-equivalent total). A `subscriptions_total` MQTT sensor
(monthly equivalent of active subs) completes spec §30.11. UI: a Subscriptions
page (monthly cost, table with per-row status, "Detect now", delete). Per-vendor
alerts (amount-changed / not-seen-when-expected, §20.3) are deferred.

## AI assistant (Stage 9 / §22)

**Off by default** and **suggestion-only** — AI never writes a category itself
(spec §22.1, §43); routing stays rules → vendor → keyword first. `ai_service` is
the single gateway: it gates by **privacy mode** (`strict_local`/`no_ai` → refuse;
`local_llm` → on-device call; `cloud_manual` → per-call approval; `cloud_auto` →
auto), redacts cloud payloads through `redaction.redact_for_cloud` (the one choke
point — description/amount/currency/candidate-categories only), and **audits
every call** to `AIRequest` (provider/model/task/mode/approval/payload/response/
status, spec §22.6). `ai_provider` has `NoAIProvider` + `OpenAICompatibleProvider`
(httpx → `/chat/completions`, JSON-validated) which covers Ollama / LM Studio /
llama.cpp / HA LLM / cloud — local vs cloud is just base URL + key. The API key
is **env-only** (`HAFI_AI_API_KEY`), never in the DB; endpoint/model are DB
settings. API: `GET /api/ai/status`, `POST /api/ai/classify/{txn}?approve=`,
`GET /api/ai/requests`. UI: an AI Settings card (mode/provider/URL/model) and a
"✨ suggest" link on uncategorised transactions (shows rationale + confidence;
the user confirms to apply via the normal manual-categorise path).

**Batch auto-apply (local only):** `classify_batch` runs the local LLM over many
uncategorised transactions and returns suggestions; `apply_suggestions` applies
the user-approved ones (treated as manual → confidence 1.0). It's **`local_llm`
only** (auto-batching to cloud would bypass per-call approval) and never applies
silently — the UI (`AiBatchPanel`) pre-ticks high-confidence rows by a threshold,
but the user clicks Apply. API: `POST /api/ai/classify-batch?limit=`,
`POST /api/ai/apply`.

**Cloud AI approval (Stage 10 / §22.5, §28):** in `cloud_manual` mode
`classify_transaction` always returns `approval_required` with the exact redacted
payload + a pending `AIRequest` (now carrying `transaction_id`) + a
`cloud_ai_approval_required` review item — nothing is sent. The user previews and
calls `POST /api/ai/requests/{id}/approve` (`run_request` sends it, stores the
response, resolves the review item) or `/reject` (`reject_request`, nothing
sent). **Sensitive-category blocking:** cloud classification refuses a
transaction whose category is `never_cloud` (§28). The AI audit log is visible in
the Settings AI card. Only `classify_transaction` is implemented;
enrich_vendor/parse_receipt/match_receipt are deferred.

## PDF statement import (Stage 11 / §11)

`parsers/generic_pdf.py` adds a PDF path to the existing parser interface.
`GenericPdfParser` extracts text with **pypdf** (the optional `ocr` extra; absent
on a bare dev box → a clear error, works in the add-on) and hands it to the pure,
unit-tested `parse_statement_text`: per line, a leading date + the **first** money
amount (a trailing balance is ignored) + the text between as the description;
`CR`/leading `+` = credit, else debit. PDF layouts vary and text extraction loses
columns, so it's **review-heavy** — every extracted row sets
`StandardTransaction.needs_review=True`, which the import flags on the
transaction (`review_reason="pdf_unverified"`) so the user verifies them
(Transactions → "Needs review"). Detection: `detect_parser` routes `%PDF-` /
`.pdf` to `generic_pdf`; `create_import` stores the file and `source_format` by
`parser.format`. Import UI accepts `.pdf`. (Scanned/image PDFs yield no text →
clear error; OCR-of-PDF is future.)

## Receipts + OCR & review queue (Stage 8 / §21, §23)

**Receipts** (`receipt_service`): upload → store original under the private
`<data>/receipts/` dir (dedup by content hash) → optional OCR → field extraction
→ match to a transaction → review item if uncertain (spec §21.1). OCR is
**optional** and split in two: `ocr_service` does image→text (Tesseract via the
`ocr` extra + the tesseract binary) and PDF→text (pypdf), reporting availability
and degrading to "skipped" + manual entry when absent (Windows dev has no
engine); `receipt_parser` is a **pure** text→fields function (merchant/date/
total/VAT/currency) so it's fully unit-tested without an engine. Matching follows
spec §21.4 (amount 50 / date 20 / vendor 20; ≥90 auto if `receipt_match_mode=auto`,
≥70 suggest, else unmatched → review item). API: `/api/receipts`
(upload/list/get/ocr/match/confirm-match/patch/delete) + `/api/receipts/status`.
UI: a Receipts page (upload, per-receipt manual fields, "Find match" → confirm).
Receipt **line items** (§21.2 level 2) and "apply items to a split" are deferred.

**Review queue** (`review_service`, spec §23): a central list of things the app
wasn't sure about (`ReviewItem`: unknown_vendor, low_confidence, duplicate,
receipt_unmatched, …). Services call `add()` (de-duped per type/id/reason);
receipts file/resolve items through it. API: `GET /api/review`, `PATCH /api/review/{id}`
(resolve/ignore), `GET /api/review/count`; a Review Queue page resolves/ignores.
Note: the dashboard's existing `review_items` count is transaction-level
(`needs_review`), distinct from the `ReviewItem` queue.

## Budgets + MQTT (Stage 6 — spec §19, §27)

`budget_service` caps spend over a period. Three flavours (spec §19.1):
**category** (`category_id`), **project** (`project_id`), or **total** (neither).
Spend is base-currency, split-aware (reuses `split_service.split_base_amount`),
debits only, excludes transfers/duplicates. Periods: weekly/monthly/quarterly/
yearly/custom (`period_bounds`). Status (§19.2): `over` > 100%, `warn` ≥ alert
threshold (default 80%), else `ok`. API: `/api/budgets` CRUD + `/api/budgets/summary`.
UI: Budgets page with a month picker, status-coloured progress bars, and a create
form. Rollover (§19.4) and "unusual vs last month" alerts are deferred.

`mqtt_service` publishes finance metrics as **Home Assistant MQTT discovery**
sensors (spec §27): a retained discovery config per sensor at
`<prefix>/sensor/finance/<object_id>/config` + retained state at
`<base_topic>/state/<key>`. Sensors = 5 core (spend/income/net this month,
review_items, uncategorised) + 2 per budget (percent, spent). **Off by default**
(strict-local); `paho-mqtt` is the optional `mqtt` extra (no-op + reported
unavailable if missing, like SQLCipher on Windows). Publishing is **best-effort**
via `publish_safe` (a broker hiccup never breaks an import or startup); triggered
on **app startup**, **import confirm**, and **budget create/update/delete**
(spec §27.1). Payload builders (`build_state`/`build_discovery`) are pure and
broker-free for testing. Broker config is env/add-on-option driven (`mqtt_host`
default `core-mosquitto`, port, optional user/pass). `/api/mqtt/{status,preview,publish}`;
Settings has an MQTT card with a "Publish now" button. The subscriptions-total
sensor (spec §30.11) waits for recurring detection (Stage 7).

## Encryption (#15 — DONE)

- **Encrypted backups** (`crypto_service`): passphrase AES-256-GCM + scrypt,
  pure-Python, works everywhere.
- **At-rest DB encryption** (`security_service`, SQLCipher): optional; the engine
  in `db/session.py` is lazy/rebindable (plaintext default; SQLCipher creator
  when enabled). Lock/unlock lifecycle = `main` lifespan + a 423 middleware while
  locked; `/api/security/{status,unlock,enable,disable}`; UI unlock gate. Two
  unlock modes (prompt / stored `HAFI_DB_KEY`). `sqlcipher3-binary` has **no
  Windows wheel** → optional `encryption` extra (installed in the add-on
  Dockerfile); on Windows the app runs plaintext and the at-rest tests skip.
  Built + verified in **WSL** (uv Python 3.12 + sqlcipher3). Lost key = data loss.

### WSL verification env (for SQLCipher / Linux checks)

Use the `Ubuntu-20.04` distro as root (`wsl -d Ubuntu-20.04 -u root`); the
default distro is Docker's and has no userland. Python 3.12 was provisioned via
**uv** (deadsnakes doesn't serve focal); venv at `/opt/hafi-venv`
(`pip install -e backend[dev] sqlcipher3-binary`). Run Linux tests:
`/opt/hafi-venv/bin/python -m pytest` from `/mnt/c/.../backend`.

## Multi-user & access control (Stage 12-S1 — spec §6, §8.2, §28; #82/#126/#74)

Identity is **not** owned by this app — Home Assistant authenticates the user at
the ingress edge and forwards `X-Remote-User-Id` / `-Name` / `-Display-Name`.
`auth_service.resolve_current_user` maps that header to a `User` row; with **no
header** (standalone/local dev) it falls back to a single `"local"` owner, so the
old single-user behaviour is unchanged. An upgraded install's pre-existing
single-user row (null `external_id`) is **adopted** as the local owner rather than
duplicated.

- **Bootstrap rule:** the first user ever seen → `owner` + `approved`. Everyone
  after → `member` + `pending` (no access until the owner approves — #126).
- **Roles** (`models/user.py`): `owner` (admin), `member` (read/write), `viewer`,
  `child` (read-only). `WRITE_ROLES = {owner, member}`, `ADMIN_ROLES = {owner}`.
- **Enforcement** is a single `_auth_guard` middleware in `main.py` (runs after
  the lock guard; skips when the DB is locked): resolves the user, stashes
  `request.state.user_*`, then for `/api/*` (except `/health`, `/security`,
  `/users/me`) returns **403** if the account isn't `approved`, and **403
  read-only** if a non-`WRITE_ROLES` user issues a non-GET. Per-route admin
  actions additionally use the `require_owner` dependency.
- **API** `routes_users`: `GET /users/me` (always reachable), `GET /users`
  (owner), `PATCH /users/{id}`, `POST /users/{id}/approve`, `DELETE /users/{id}`.
  Guard (#74): can't demote/disable/delete the **last active owner**; the role is
  always read from the stored row, never trusted from the client.
- **Audit:** `audit_service.record()` (new — the `audit_logs` table existed but
  was unwritten) logs user-management actions; reused for failed-unlock/sensitive
  actions in S3.
- **UI:** owner-only **Users** page (approve queue + role/status/remove); a
  pending/disabled user sees an `AccountGate` instead of the app; the Users nav
  item is owner-only. Migration `d1e2f3a4b5c6` adds `users.external_id`/`status`/
  `last_seen_at`.
### MFA / TOTP (Stage 12-S2 — #124)

Optional per-user second factor on top of HA auth. TOTP is implemented in-house
(`services/totp.py`, RFC 6238, stdlib only — no dependency) so it works on every
platform and matches Google Authenticator / Aegis / 1Password defaults (SHA-1, 6
digits, 30s).

- **Enrol:** `POST /auth/mfa/setup` stores a base32 secret (`users.mfa_secret`)
  and returns the otpauth URI; `mfa_enabled` flips true only after `enable`
  confirms a code. `disable` (code required) clears the secret + all sessions.
- **Entry gate:** a user with MFA on must `POST /auth/mfa/verify` a code, which
  mints a per-device session — a random token whose **SHA-256 hash** is stored in
  `user_sessions` (raw token lives in the browser, sent as `X-HAFI-Session`).
  The `_auth_guard` middleware blocks data APIs with 403 `{mfa_required:true}`
  until a valid, unexpired session is presented. `/auth/mfa/*` is exempt (so the
  user can verify) but still approval-gated. `/users/me` reports `mfa_required`
  so the SPA shows the gate.
- **Admin step-up (#124 "re-enter for admin stuff"):** owner mutations use the
  `require_owner_step_up` dependency — if the owner has MFA on and their session's
  `last_step_up_at` is older than `STEP_UP_TTL` (10 min), it returns 403
  `step_up_required`; `POST /auth/mfa/step-up` refreshes it. The Users page
  catches that error, prompts for a code, and replays the action.
- **TTLs:** session `SESSION_TTL` 12h; step-up 10 min. Sessions are per-device,
  cleared on disable, and (being in the DB) protected by at-rest encryption.
- **UI:** an MFA entry gate (App), a Settings "Two-factor" card (setup shows
  secret + otpauth URI; enable/disable), and step-up handling on the Users page.
  Migration `e2f3a4b5c6d7` adds `users.mfa_secret`/`mfa_enabled` + `user_sessions`.
- **Note:** the secret sits in the DB (standard for TOTP); at-rest encryption is
  the protection for a stolen disk. No QR image yet — the otpauth URI/secret are
  shown for manual entry.

### Security health + failed-unlock alerts (Stage 12-S3 — #128/#130)

- **Failed-unlock tracking:** unlock attempts happen while the DB is *locked*
  (encrypted, not yet opened), so the app DB is unavailable — failures are logged
  to a small JSON file next to the DB (`security_events.json`, last 50) via
  `security_service.record_failed_unlock()`. `record_successful_unlock()` clears
  the streak. `failed_unlock_summary()` (rolling 60-min window) is included in
  `/security/status`, so the unlock screen shows "N failed attempts in the last
  hour" and the structured log warns on each failure.
- **Security-health panel** (`security_health_service.evaluate`): owner-only
  checks — at-rest encryption (off → warn / unavailable → info / stored-key →
  info), MFA on the owner's account, ≥3 recent failed unlocks, users awaiting
  approval, and `cloud_auto` AI. Each is `{severity, recommendation, actionable,
  active}`. **Non-nagging:** every warning can be dismissed (forever) or snoozed
  N days; dismissals live in a `security_dismissals` settings row (an expired
  snooze reappears). `GET /security/health` + `POST /security/health/dismiss`
  (both `require_owner`).
- **UI:** a "Security health" card in Settings (recommendations + dismiss/snooze/
  restore), a one-line owner-only banner on the Dashboard when `active_count > 0`
  linking to Settings, and the failed-attempt note on the unlock gate. No
  migration (uses the settings table + the JSON file).

### Hardening pass (Stage 12-S4 — #74)

Adversarial test suite (`tests/test_security_hardening.py`) pinning the negative
cases: a member can't manage users or self-promote; **forged identity headers**
(`X-Remote-User-Role`, etc.) confer nothing — a new identity is only ever pending,
and the role is read from the stored row; MFA **session tokens are bound to their
user and expiry** (a foreign/forged/expired token is rejected); invalid
role/status values are 400; disabled accounts and the read-only `child` role are
enforced. Documented the **trust boundary** in docs/security.md: identity is only
as good as the ingress proxy, so the add-on stays ingress-only — don't expose the
raw port. Also refreshed the stale "unencrypted at rest" note (at-rest encryption
now exists, optional).

The security/multi-user cluster (S1–S4) is **complete**.

## Trends & outliers (Stage 12 — spec §24.12, §37; #146, #150)

`analytics_service` (read-only, base-currency, dashboard-consistent — transfers/
duplicates excluded, split-aware category figures):

- **`monthly_series(ref, months)`** → `GET /api/dashboard/monthly?months=N` (N
  clamped 2–24): spend/income/net per month (oldest→newest) + a `trend` summary
  comparing the latest month to the previous one (`delta`, `pct`, `direction`
  up/down/flat). Drives the dashboard sparklines + arrows.
- **`outliers(ref)`** → `GET /api/dashboard/outliers`: a "heads-up" list from four
  detectors — **large charges** (≥3× the median charge over a 6-month lookback,
  needs ≥8 charges to set a baseline), **category spikes** (this month >1.5× and
  ≥£30 over the prior-3-month average, needs ≥2 months of history), **new
  merchants** (not seen in the prior 3 months, ≥£20, skipped when there's no
  history), and **budget alerts** (reuses `budget_service.summary` warn/over).
  Each item is `{type, severity, title, detail, amount, …ids}`. **Conservative by
  design + gated on history** so a fresh import doesn't light up with false
  positives (see `test_no_false_positives_without_history`).
- **UI:** a "Trends" card (3 inline-SVG sparklines, no chart dep) and a non-nagging
  "Heads-up" card on the Dashboard that only renders when there's something to
  flag. No new tables/migration — pure analytics over existing data.

## Open questions / to scope

- **#78 Data retention/expiry** — purge vs archive? which data (transactions,
  receipts, AI logs)? per-category retention?
- **#29 FX coverage** — Frankfurter is ECB (~30 currencies); add a wider source
  (e.g. fawazahmed0 API) if exotic currencies are needed.

## Working agreements

- Strict-local by default; anything that leaves the device is opt-in and audited.
- Ask the owner when a decision is genuinely theirs; otherwise pick a sensible
  default and say so.
- Verify changes (tests + a live run) before claiming done.
