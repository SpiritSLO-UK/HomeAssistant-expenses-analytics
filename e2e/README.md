# End-to-end UI tests (Playwright)

Open-source ([Playwright](https://playwright.dev), Apache-2.0) browser tests that
drive the real app and produce an HTML report. They run against a running
instance that serves both the SPA and the API from one origin (the standalone
build), which runs single-user as a local owner, so no login step is needed.

## Run locally against the demo

Bring the demo up first (`docker compose up -d --build` from the repo root, then
load sample data once via `POST /api/backup/demo`), then:

```bash
cd e2e
npm ci
npx playwright install chromium      # one-time browser download
npm test                             # runs against http://127.0.0.1:8099
npm run report                       # open the HTML report
```

Point at a different instance with `E2E_BASE_URL`, e.g.
`E2E_BASE_URL=http://127.0.0.1:9000 npm test`.

## What it covers

Render checks:

- `smoke.spec.ts` - every top-level page renders its heading with no JS error or server 5xx.
- `new-features.spec.ts` - search filter-token hints, audit CSV export, the Settings Tags card.
- `modals.spec.ts` - the in-app confirm dialog (open, Cancel, Escape).
- `forecasts.spec.ts` - budget pace, project burn-down forecast, savings sparkline + goals.
- `transactions.spec.ts` - filters, CSV export, range switching.
- `import.spec.ts` - a US-format CSV previews with month-first dates parsed correctly.
- `savings-deposit.spec.ts` - the deposit confirm modal states the resulting balance before applying (opens + cancels; non-destructive).
- `search-keyboard.spec.ts` - the global-search keyboard nav (no highlight for mouse users until an arrow key; arrows move the active descendant; Enter opens) and a category chip deep-linking into filtered Transactions.
- `import-mapping.spec.ts` - the "Map columns (custom CSV)" panel maps columns from the file headers, the date-order selector offers Auto/Day-first/Month-first, and Month-first parses `6/28/2026` as `2026-06-28` (preview only; non-destructive).
- `architecture-doc.spec.ts` - `docs/architecture.html` renders its inline-SVG diagrams over `file://` with no off-origin requests.

These automate the release UI-test walkthrough (`docs/ui-test-guide.md`). The AI batch
panels, Review Queue bulk-AI, and MFA backup codes need a live AI key / TOTP secret and
are not automated here; the opt-in security-event MQTT publish is an env/add-on option with
no runtime UI toggle and is covered by `backend/app/tests/test_mqtt.py`.

Task flows (really doing things; every mutating flow is **self-cleaning**, so the
database ends each run exactly as it started):

- `tasks.spec.ts` - create + delete a category, budget, rule (incl. clone),
  vendor (with alias) and savings goal; flip the log-level select optimistically
  and verify it persists; a **full CSV import** (preview, confirm, rows visible in
  Transactions, then removed via `DELETE /api/imports/{id}`); a `category:` token
  search returning results.
- `tasks-extra.spec.ts` - theme dark/system switch; project create + delete;
  account create (cleaned up via API); the Users admin surface; subscriptions
  "Detect now"; a **garbage CSV fails gracefully** (error banner, no crash); the
  bulk "+ tag" FE-10 prompt opens and cancels; an unused tag removed end-to-end
  through the Settings Tags card. An `afterEach` sweeper deletes any stray
  `E2E*` accounts/tags even when a test fails mid-flow.

## Notes

- Runs **serially** (`workers: 1`): the app is one instance backed by a single
  SQLite database, and the task flows mutate shared state, so ordering stays
  deterministic and the report stable.
- CI (`.github/workflows/ci.yml`, `E2E` job) builds the image, boots it, seeds
  demo data, runs the suite, and uploads the HTML report as an artifact on every
  PR and push to main.
- Releases (`.github/workflows/release.yml`, `E2E report (release)` job) run the
  suite against the release build on every version tag and attach the zipped
  HTML report to the GitHub Release (or keep it as a 90-day workflow artifact if
  the release doesn't exist yet).
