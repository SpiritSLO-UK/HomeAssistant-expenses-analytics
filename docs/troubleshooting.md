# Troubleshooting

Common problems and fixes. If something here doesn't resolve it, turn on debug
logging (Settings → Logging → `DEBUG`, or `HAFI_LOG_LEVEL=DEBUG`) and check the
logs, then open an issue with the relevant lines (redact any account details).

> Reminder: everything is local. Most "it didn't reach the internet" symptoms are
> **expected** — AI, online FX, the investment price feed and Paperless are all
> **off by default** and opt-in.

---

## Install & startup

**The page won't load / connection refused.**
- Standalone: confirm the container is up (`docker compose ps`) and hit
  **`http://127.0.0.1:8099`** — use the IPv4 address, not `localhost` (which can
  resolve to IPv6 `::1` first and time out).
- Check logs: `docker compose logs -f` (standalone) or the add-on **Log** tab.

**"Frontend not built" message at `/`.**
- The backend serves the built `frontend/dist`. In the published image it's
  prebuilt; if you're running from source, build it: `npm run build` in
  `frontend/` (Node 20+).

**Add-on won't start after changing options.**
- Check the **Log** tab for a config/schema error. `privacy_mode` and `log_level`
  must be one of the allowed values (see [configuration.md](configuration.md)).

---

## Database locked / encryption

**"Database is locked. Unlock with your passphrase."**
- At-rest encryption is enabled in "prompt" mode. Enter your passphrase on the
  unlock screen. There is **no recovery** if the passphrase is lost — by design.
- To start unattended instead, use "stored" mode and set `HAFI_DB_KEY`. See
  [security.md](security.md).

**Repeated failed-unlock warnings.**
- These are recorded (capped) and surfaced as a count. If unexpected, treat it as
  a sign someone is trying passphrases and rotate it.

---

## Importing statements

**"Already imported" / rows skipped.**
- Duplicates are detected by a content hash of each row (account, date, amount,
  currency, description). Re-uploading the same statement is safe — matching rows
  are skipped, not duplicated.

**A CSV won't parse / columns look wrong.**
- The importer auto-detects common bank layouts. If yours isn't recognised,
  check the column headers against the samples in
  [`examples/sample-csv/`](../examples/sample-csv/). Date formats are parsed
  flexibly but ISO (`YYYY-MM-DD`) is safest.

**A scanned/photo statement imported little or nothing.**
- OCR is best-effort and each extracted row is flagged for review. Scanned PDFs
  are rasterised first; very low-quality scans may not read. Verify/fix rows in
  the review queue, or enter them manually.

**Curve statements show duplicates of my other cards.**
- Curve is a pass-through ("transient") card: each payment is charged to one of
  your real cards, so the same purchase also appears on that card's own
  statement. When you preview a Curve export, a **Curve funding cards** panel
  lists each `Card Name` it found — map each one to the real account behind it.
  After that, importing both statements skips the duplicate: a bank row tagged
  `CURVE`/`CRV*…` is skipped automatically, and an amount+date match without that
  tag (within a few days) is kept but flagged for review so you can decide.
- Leave a card **unmapped** (e.g. `Curve Cash`) to import its rows normally.

**Curve Cash (rewards).** Curve Cash is Curve's cashback programme, in `CPT`
points (1 CPT = 1p). Rows like `Curve Cash: Lidl` are cashback you **earned** →
imported as **income** in the **Cashback** category. A real merchant funded by
Curve Cash (it carries a GBP *Foreign Spend* value) is treated as a normal
**spend** in that category. So earning books as money in and spending it books as
money out — they net out, which is the correct accounting.

---

## Categorisation & rules

**Transactions aren't being categorised.**
- AI is off by default, so categorisation is **rule-based + vendor defaults**.
  Add rules (see [rules.md](rules.md)) or set a vendor's default category on the
  Vendors page. Use **Re-categorise** to re-apply rules to existing rows.

**A rule isn't matching what I expect.**
- Check precedence and the match type (contains / regex / …) in
  [rules.md](rules.md). The in-app "How rules work" panel on the Rules page has
  worked examples.

---

## OCR (receipts)

**OCR isn't reading receipts.**
- OCR runs only in the add-on image (Tesseract is bundled there). Standalone
  images may not include it — then receipts fall back to **manual entry** (the
  fields are editable). Confirm OCR is on in Settings → Services.
- A receipt that reads poorly is flagged for review; correct the merchant/date/
  total by hand and match it to a transaction.

---

## Multi-currency / FX

**Foreign transactions show no base amount / "needs rate".**
- A foreign-currency row needs an FX rate before it counts toward base-currency
  totals. Either enter a manual rate, or enable **online FX** (Settings →
  Services → online rates = Frankfurter) and use **Sync** to backfill missing
  rates. Online FX is free, no key, ECB data — but off by default.

**Changing the base currency didn't change stored amounts.**
- That's correct: amounts are stored in their original currency. Changing the
  base only recomputes the **display** conversion; it never rewrites stored rates.

---

## AI

**The assistant makes no suggestions.**
- Expected when AI is off (`strict_local` / `no_ai`). To enable: pick a real
  `privacy_mode`, choose a provider + model in Settings → AI, and (for cloud)
  set `HAFI_AI_API_KEY`. Sensitive categories can still be blocked from cloud.

**Cloud AI says "not configured".**
- A mode is selected but no provider/endpoint/key is set. Finish the AI card in
  Settings, and ensure the API key env var is present.

---

## MQTT / Home Assistant sensors

**No finance sensors appear in Home Assistant.**
- MQTT is off by default. Enable it (`mqtt_enabled` / `HAFI_MQTT_ENABLED=true`),
  point it at your broker (the Mosquitto add-on is `core-mosquitto`), and check
  the log for publish errors. Sensors use MQTT discovery and appear under the
  configured base topic.

---

## Investments price feed

**"Sync prices" does nothing.**
- The source is `manual` (default) — no network. Switch it (Settings or the
  Investments page) to `stooq` (keyless; use suffixed tickers like `aapl.us`,
  `vwrl.uk`) or `alphavantage` (set `HAFI_INVESTMENT_API_KEY`). A symbol that
  can't be resolved is counted as "not found" and its price is left unchanged.

---

## Paperless import

**The "Import from Paperless" card isn't on the Receipts page.**
- By design — it only appears once Paperless is configured. Set the **token**
  (`HAFI_PAPERLESS_TOKEN`, a secret) and a **URL** (env `HAFI_PAPERLESS_URL` or
  Settings → Integrations), then restart if you changed env. Settings →
  Integrations shows the live status and a **Test connection** button. Full
  steps: [configuration.md → Paperless-ngx setup walkthrough](configuration.md).

**"Could not reach Paperless" (502) / Test connection fails.**
- Check the URL is reachable from the app's container/network and the token is
  valid. The flow is one-directional (we only request from Paperless), and imports
  de-duplicate by content, so retrying is safe.

---

## Performance

**Aggregates feel slow on a large ledger.**
- The hot columns are indexed (transactions/splits by account, date, category,
  vendor, project, archived). If you imported a very large history, the first
  page load builds caches; subsequent loads are fast. SQLite is light by design —
  see [architecture.md](architecture.md).
