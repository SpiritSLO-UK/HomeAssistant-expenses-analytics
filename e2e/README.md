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

- `smoke.spec.ts` - every top-level page renders its heading with no JS error or server 5xx.
- `new-features.spec.ts` - search filter-token hints, audit CSV export, the Settings Tags card.
- `modals.spec.ts` - the in-app confirm dialog (open, Cancel, Escape).
- `forecasts.spec.ts` - budget pace, project burn-down forecast, savings sparkline + goals.
- `transactions.spec.ts` - filters, CSV export, range switching.
- `import.spec.ts` - a US-format CSV previews with month-first dates parsed correctly.

## Notes

- Runs **serially** (`workers: 1`): the app is one instance backed by a single
  SQLite database with a bounded connection pool, so parallel workers would
  exhaust it. This matches real single-user usage and keeps the report stable.
- The suite is non-destructive: it previews imports without confirming and always
  cancels delete dialogs, so it does not mutate the demo data.
- CI (`.github/workflows/ci.yml`, `E2E` job) builds the image, boots it, seeds
  demo data, runs the suite, and uploads the HTML report as an artifact.
