# UI test walkthrough (release QA)

A click-by-click pass to verify the user-visible changes on `main` before cutting
the next release. It covers the recently-touched features: in-app modals,
optimistic select-on-change, the budget/project/savings forecasts, MFA backup
codes, global search, the AI batch panels, Rules clone and reorder, Categories
accessibility and bulk recolour, the Review Queue, CSV export, and a US-format
CSV import. Budget ~20-30 minutes for the whole list.

Each item has a short "What changed", numbered steps, and checkbox expected
results. Tick as you go; anything that fails is a release blocker to note (open
issues go into the private backlog tracker).

## Setup

1. Open the app at http://127.0.0.1:8099 (the local demo). The demo ships with
   sample transactions, categories, budgets, projects and savings, so most
   screens have data to work with already.
2. AI features (the "AI categorise" panels and the per-row "suggest") need an AI
   key in **Settings -> AI** (env `HAFI_AI_API_KEY`, or set one from the UI on a
   standalone instance). If AI is off, skip the AI steps or configure a key first.
3. Note: the app is a single-page app. If a screen looks stale after an action,
   it should refresh on its own; a manual reload should never be required. (After
   an *update*, an open tab auto-reloads once to pick up new assets.)

- [ ] App loads at http://127.0.0.1:8099 with sample data visible on the Dashboard.

## In-app modals (confirm / prompt / alert)

**What changed:** every native browser `confirm` / `prompt` / `alert` was
replaced by an in-app modal (so it also works inside the Home Assistant ingress
iframe, which can suppress native dialogs).

1. Go to **Categories**. Click the delete (trash) control on a category you do
   not mind removing.
2. Observe the dialog that appears, then click **Cancel**.
3. Go to **Transactions**. On any row, use the tag control to add a tag; a text
   prompt modal should appear asking for the tag name.

- [ ] Delete shows a styled in-app confirmation modal (not a grey browser popup),
      with a red/destructive confirm button.
- [ ] Cancel closes it and nothing is deleted.
- [ ] The "add a tag" action shows an in-app text-entry modal with an input field.
- [ ] No native browser dialog appears anywhere during the pass.

## Transactions: optimistic select-on-change and bulk edit

**What changed:** the per-row selects (category, vendor, project, country) apply
instantly and revert if the save fails. Bulk value-changes (from the selection
toolbar) now confirm "Apply ... to N transaction(s)?" before applying.

1. Go to **Transactions**.
2. On a row, change the **category**, then **vendor**, **project**, **country**.
3. Tick several rows to open the bulk-actions bar; set a category/project/country
   or mark business, and confirm the "Apply to N" dialog.

- [ ] The new value appears immediately, with no visible "loading" gap, and sticks
      after the list refreshes.
- [ ] A bulk value-change asks "Apply ... to N transaction(s)?" before applying.

## Transactions: export filtered set to CSV

**What changed:** the toolbar Export CSV button downloads the *filtered* set (not
just the current page).

1. On **Transactions**, apply a filter (a date quick-range or a search term).
2. Click **Export CSV** (the down-arrow button near the top of the list).

- [ ] A `transactions.csv` file downloads containing the filtered rows (not only
      the visible page).

## Transactions: tags (add, remove, bulk)

**What changed:** tags are added and removed inline; a bulk "+ tag" applies a tag
to every selected row.

1. On a single row, add a tag via the tag control, then remove it with its (x).
2. Tick a few rows, use the **+ tag** action, and enter a tag name.

- [ ] The tag appears/disappears on the row immediately.
- [ ] The bulk "+ tag" prompt is an in-app modal (with the selected count) and
      applies the tag to all selected rows.

## AI batch categorise panels

**What changed:** there are **two** AI batch panels, and which one you see depends
on your privacy mode (Settings -> AI). They differ by design:

- **Local** `✨ AI categorise…` (only in **`local_llm`** / on-device mode): a header
  **"select all"** checkbox (with indeterminate state) + **Export CSV** of the
  suggestions -> `ai-suggestions.csv` (Description, Suggested category, Confidence).
- **Cloud** `☁️ AI categorise (cloud)…` (in **`cloud_manual` / `cloud_auto`**):
  review the redacted "Will send" list -> **Send to cloud** -> suggestions
  pre-ticked by a confidence threshold -> **Apply**. It also has **Export CSV**
  (the will-send list -> `cloud-ai-will-send.csv`, and the suggestions once
  returned).

> The plain Transactions **toolbar** "Export CSV" is a THIRD, unrelated thing: it
> exports your filtered **transactions**, not AI suggestions. Don't confuse it
> with the panel exports.

**Local panel** (set Settings -> AI to `local_llm`; click `✨ AI categorise…`):
- [ ] The header checkbox selects/clears every suggestion (indeterminate when some).
- [ ] Export CSV downloads `ai-suggestions.csv` (description, suggested category, confidence).

**Cloud panel** (`cloud_manual`; click `☁️ AI categorise (cloud)…`):
- [ ] The "Will send" list shows only a redacted description + amount per row.
- [ ] Export CSV of the will-send list (and of the suggestions after Send) works.
- [ ] Apply writes the ticked categories.

## Review Queue: bulk AI + resolve

**What changed:** the Review Queue can bulk "AI suggest + categorise", and each
item can be resolved or ignored inline.

1. Go to **Review Queue**.
2. If items are present and AI is configured, use the bulk AI suggest/categorise.
3. On an individual item, click **Resolve** (and try **Show resolved**).

- [ ] Bulk AI categorises the open items (with a key configured).
- [ ] Resolve clears the item; **Show resolved** reveals resolved items.

## Budgets: pace signal

**What changed:** each within-budget budget shows a prorated "pace" line (ahead of
pace / on pace / behind pace) alongside the over/near-limit/on-track status.

> By design, a budget that is already **over budget** hides the pace line (it
> would be redundant) - so pace shows on some budgets and not others. That is
> expected, not a bug.

1. Go to **Budgets** (or the Budgets card on the Dashboard).
2. Read the status and pace line on each budget.

- [ ] Within-budget budgets show a pace label ("on pace" / "ahead of pace" / "behind pace").
- [ ] A budget past 100% reads as "over budget" (and shows no pace line).

## Projects: burn-down forecast

**What changed:** a project with a budget shows a forecast line: on track / over
budget, a run-rate per day, a projected total, and an expected budget-spent date.

1. Go to **Projects**. Find a project with a budget and read its forecast line.

- [ ] The forecast reads "on track" (green) or "over budget" (red).
- [ ] Where applicable it shows a run-rate per day, a projected total, and/or the
      forecast budget-spent date.

## Savings: deposit/withdraw and goal forecast

**What changed:** deposit/withdraw is confirmed with the resulting balance, and
each goal shows a deposit-rate / time-to-goal forecast (with an interest-only
projection when there's no deposit history, and a bounded state for very slow
goals). A goal linked to an account in another currency is converted.

1. Go to **Savings**. Enter a small deposit and confirm; read the resulting balance.
2. Look at a savings goal (including a newly-created one) and read its forecast.

- [ ] The deposit/withdraw confirmation states the resulting balance before you apply.
- [ ] A goal shows a forecast (or a clear "add deposits" state), and the savings
      page never errors on a slow-progress goal.

## Rules: clone and drag-to-reorder

**What changed:** a rule can be cloned, and rules can be reordered by dragging.

1. Go to **Rules**. Click **Clone** on a rule; confirm a suffixed copy appears.
2. Drag a rule row to a new position.

- [ ] Clone creates a duplicate with the same condition/action and a suffixed name.
- [ ] Dragging reorders the rows and the new priority persists after refresh.

## Categories: accessibility, merge, bulk recolour

**What changed:** category controls carry aria-labels; owners can merge one
category into another; several categories can be recoloured at once.

1. Go to **Categories**.
2. (Owner only) Merge a source into a target and confirm the naming modal.
3. Tick several categories, pick a bulk-recolour colour, and apply.

- [ ] Delete / colour / select controls have descriptive labels.
- [ ] Merge re-points the source's transactions onto the target and removes the source.
- [ ] Bulk recolour applies to every ticked category; Dashboard swatches update.

## Global search: keyboard navigation

**What changed:** the global search is keyboard-navigable and its result groups
show clickable category/vendor chips that deep-link into filtered transactions.

1. Open **Search**. Type at least two characters.
2. Use Down/Up to move the highlight, then Enter to open the highlighted result.
3. Back on Search, click a category or vendor chip.

- [ ] Arrow keys move a visible highlight; Enter opens the highlighted result.
- [ ] A category/vendor chip navigates to Transactions filtered by that entity.

## Settings: MFA backup codes

**What changed:** with MFA enabled, Settings shows a backup-codes section to
generate, copy and download one-time recovery codes.

> To test this, first enable MFA for your user in **Settings -> Security** (scan
> the TOTP QR with an authenticator app and confirm a code). The backup-codes
> section only appears once MFA is enabled. Skip this section if not testing MFA.

1. Go to **Settings**. Read the "N unused backup codes remaining" line.
2. Generate a new set, then copy to clipboard and download the `.txt`.

- [ ] The section reports how many unused backup codes remain.
- [ ] Generating shows a fresh set with a "save them now" warning.
- [ ] Copy-to-clipboard and download `hafi-backup-codes.txt` both work.

## Import: custom CSV column mapping and US-format dates

**What changed:** a "Map columns (custom CSV)" panel imports any bank CSV, with a
date-order selector that supports US month-first dates (e.g. 6/28/2026).

> A ready-made US-format sample lives at
> [examples/sample-csv/us-chase-sample.csv](../examples/sample-csv/us-chase-sample.csv)
> (month-first `M/D/YYYY` dates, single signed Amount column).

1. Go to **Import**. Choose `us-chase-sample.csv`, open **Map columns (custom CSV)**.
2. Map the date, amount and description columns; set date order to **Month-first MM/DD**.
3. Preview and confirm the import.

- [ ] The column-mapping panel maps each column from the file's headers.
- [ ] The date-order selector offers Auto, Day-first DD/MM and Month-first MM/DD.
- [ ] With Month-first, `6/28/2026` imports as 28 June 2026 (not rejected or month 28).

## Docs and architecture (browser check)

**What changed:** per-package READMEs distinguish the add-on from the standalone
build, and `docs/architecture.html` renders the architecture diagrams offline.

1. Open `docs/architecture.html` directly in a browser.

- [ ] The architecture diagrams render (inline SVG) with no network access.

---

When every box is ticked, the unreleased UI changes are verified and the release
can be cut. Note any failures with the page name and what you saw; open issues go
into the private backlog tracker.
