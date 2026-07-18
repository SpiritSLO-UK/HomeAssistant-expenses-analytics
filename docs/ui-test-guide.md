# UI test walkthrough (release QA)

A click-by-click pass to verify the user-visible changes on `main` before cutting
the next release. It covers the recently-touched features: in-app modals,
optimistic select-on-change, the budget/project/savings forecasts, MFA backup
codes, global search, the AI batch panel, Rules clone and reorder, Categories
accessibility and bulk recolour, the Review Queue, CSV export, and a US-format
CSV import. Budget ~20-30 minutes for the whole list.

Each item has a short "What changed", numbered steps, and checkbox expected
results. Tick as you go; anything that fails is a release blocker to note.

## Setup

1. Open the app at http://127.0.0.1:8099 (the local demo). The demo ships with
   sample transactions, categories, budgets, projects and savings, so most
   screens have data to work with already.
2. AI features (the "AI categorise" panels and the per-row "suggest") need a
   cloud AI key configured in **Settings -> AI**. If AI is off, skip the AI
   steps or configure a key first.
3. Note: the app is a single-page app. If a screen looks stale after an action,
   it should refresh on its own; a manual reload should never be required.

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

## Transactions: optimistic select-on-change

**What changed:** the per-row selects (category, vendor, project, country) apply
instantly instead of waiting for the server, and revert to the previous value if
the save fails.

1. Go to **Transactions**.
2. On a row, change the **category** using its dropdown.
3. Do the same for **vendor**, **project** and **country** on a couple of rows.

- [ ] The new value appears immediately, with no visible "loading" gap.
- [ ] The change sticks after the list refreshes (it was saved).
- [ ] (Optional; hard to trigger manually - covered by unit tests) a failed save
      snaps the field back to its previous value rather than showing a wrong value.

## Transactions: export filtered set to CSV

**What changed:** an Export CSV button downloads the *filtered* set (not just the
current page).

1. On **Transactions**, apply a filter (for example a date quick-range or a
   search term).
2. Click **Export CSV** (the down-arrow button near the top of the list).

- [ ] A `transactions.csv` file downloads.
- [ ] It contains the filtered rows, not only the visible page.

## Transactions: tags (add, remove, bulk)

**What changed:** tags are added and removed inline; a bulk "+ tag" applies a tag
to every selected row.

1. On a single row, add a tag via the tag control, then remove it by clicking its
   remove (x) control.
2. Tick the checkboxes on a few rows to open the bulk-actions bar, then use the
   **+ tag** action and enter a tag name.

- [ ] The tag appears on the row immediately and disappears when removed.
- [ ] The bulk "+ tag" prompt is an in-app modal and applies the tag to all
      selected rows.

## AI batch categorise panel (select-all + CSV export)

**What changed:** the AI batch panel gained a header "select all" checkbox and an
Export CSV of the suggestions. (Needs a cloud AI key.)

1. On **Transactions**, click **AI categorise...** (or **AI categorise (cloud)...**).
2. When suggestions load, click the header checkbox to select all, then untick one
   row (the header should go to an indeterminate state).
3. Click **Export CSV**.

- [ ] The header checkbox selects/clears every suggestion at once.
- [ ] With some but not all selected, the header checkbox shows an indeterminate
      (dash) state.
- [ ] Export CSV downloads `ai-suggestions.csv` with description, suggested
      category and confidence columns.

## Review Queue: bulk AI + resolve

**What changed:** the Review Queue can bulk "AI suggest + categorise", and each
item can be resolved or ignored inline.

1. Go to **Review Queue**.
2. If items are present and AI is configured, use the bulk AI suggest/categorise
   control.
3. On an individual item, click **Resolve** (and try **Show resolved** to confirm
   it moved).

- [ ] Bulk AI categorises the open items (with a cloud key configured).
- [ ] Resolve clears the item from the open list.
- [ ] The **Show resolved** toggle reveals resolved items.

## Budgets: pace signal

**What changed:** each budget shows a prorated "pace" line (ahead of pace / on
pace / behind pace) in addition to the over/near-limit/on-track status.

1. Go to **Budgets** (or the Budgets card on the Dashboard).
2. Read the status and pace line on each budget.

- [ ] Budgets show a pace label such as "on pace", "ahead of pace" or "behind pace".
- [ ] A budget past 100% reads as "over budget".

## Projects: burn-down forecast

**What changed:** a project with a budget shows a forecast line: on track / over
budget, a run-rate per day, a projected total, and an expected budget-spent date.

1. Go to **Projects**.
2. Find a project that has a budget set and read its forecast line.

- [ ] The forecast reads "on track" (green) or "over budget" (red).
- [ ] Where applicable it shows a run-rate per day, a projected total, and/or the
      date the budget is forecast to be spent.

## Savings: deposit/withdraw and goal forecast

**What changed:** deposit/withdraw is confirmed with the resulting balance, and
each goal shows a deposit-rate / time-to-goal forecast.

1. Go to **Savings**.
2. Enter a small deposit amount and confirm; read the confirmation showing the
   resulting balance.
3. Look at a savings goal and read its forecast line.

- [ ] The deposit/withdraw confirmation modal states the resulting balance before
      you apply it.
- [ ] A goal shows a forecast (for example months remaining, or a "behind"
      indicator when contributions are short).

## Rules: clone and drag-to-reorder

**What changed:** a rule can be cloned, and rules can be reordered by dragging to
change their priority.

1. Go to **Rules**.
2. Click **Clone** on a rule and confirm a copy appears (name suffixed).
3. Drag a rule row to a new position to change its priority.

- [ ] Clone creates a duplicate rule with the same condition/action and a
      suffixed name.
- [ ] Dragging reorders the rows and the new priority order persists after refresh.

## Categories: accessibility, merge, bulk recolour

**What changed:** category controls carry aria-labels; owners can merge one
category into another; and several categories can be recoloured at once.

1. Go to **Categories**.
2. (Owner only) Pick a merge source and target and confirm the merge modal, which
   names both categories.
3. Tick several categories, pick a colour in the bulk-recolour control, and apply.

- [ ] Delete / colour / select controls have descriptive labels (usable with a
      screen reader or keyboard).
- [ ] Merge re-points the source category's transactions onto the target and
      removes the source.
- [ ] Bulk recolour applies the chosen colour to every ticked category, and the
      Dashboard swatches update to match.

## Global search: keyboard navigation

**What changed:** the global search is keyboard-navigable and its result groups
show clickable category/vendor chips that deep-link into filtered transactions.

1. Open **Search** (or the sidebar quick-search).
2. Type at least two characters.
3. Use the Down and Up arrow keys to move the highlight through the results, then
   press Enter to open the highlighted one.
4. Back on Search, click a category or vendor chip in the results.

- [ ] Arrow keys move a visible highlight through the flattened result list.
- [ ] Enter opens the highlighted result.
- [ ] A category/vendor chip navigates to Transactions filtered by that entity.

## Settings: MFA backup codes

**What changed:** with MFA enabled, Settings shows a backup-codes section to
generate, copy and download one-time recovery codes.

> To test this, first enable MFA for your user in **Settings -> Security**
> (scan the TOTP QR with an authenticator app and confirm a code). The
> backup-codes section only appears once MFA is enabled. If you are not testing
> MFA this pass, skip this section.

1. Go to **Settings**.
2. Read the "N unused backup codes remaining" line.
3. Generate a new set, then copy to clipboard and download the `.txt`.

- [ ] The section reports how many unused backup codes remain.
- [ ] Generating shows a fresh set with a "save them now, they won't be shown
      again" warning.
- [ ] Copy-to-clipboard and download `hafi-backup-codes.txt` both work.

## Import: custom CSV column mapping and US-format dates

**What changed:** a "Map columns (custom CSV)" panel imports any bank CSV, with a
date-order selector that supports US month-first dates (for example 6/28/2026).

> A ready-made US-format sample lives at
> [examples/sample-csv/us-chase-sample.csv](../examples/sample-csv/us-chase-sample.csv)
> (month-first `M/D/YYYY` dates, single signed Amount column).

1. Go to **Import**.
2. Choose `us-chase-sample.csv`, then open **Map columns (custom CSV)**.
3. Map the date, amount and description columns.
4. Set the date order to **Month-first MM/DD**.
5. Preview and confirm the import.

- [ ] The column-mapping panel lets you map each column from the file's headers.
- [ ] The date-order selector offers Auto, Day-first DD/MM and Month-first MM/DD.
- [ ] With Month-first selected, `6/28/2026` imports as 28 June 2026 (not rejected
      or read as month 28).

## Docs and architecture (browser check)

**What changed:** per-package READMEs distinguish the add-on from the standalone
build, and `docs/architecture.html` renders the architecture diagrams offline.

1. Open `docs/architecture.html` directly in a browser.

- [ ] The architecture diagrams render (inline SVG) with no network access.

---

When every box is ticked, the unreleased UI changes are verified and the release
can be cut. Note any failures with the page name and what you saw; open issues go
into the private backlog tracker.
