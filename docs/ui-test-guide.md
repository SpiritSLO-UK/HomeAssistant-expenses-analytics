# UI test walkthrough (release QA)

A click-by-click pass to verify the user-visible changes on `main` before cutting
the next release. It covers the recently-touched features: the grouped sidebar +
Customise-navigation editor, in-app modals, optimistic select-on-change, the
budget/project/savings forecasts, edit-after-creation, MFA backup codes, global
search, the AI batch panels (incl. background cloud send), Rules clone and
reorder, Categories accessibility and bulk recolour, the dedicated Tags page, the
app-wide date format, the (config-only) security-event MQTT option, the Review
Queue, CSV export (filtered + selected), and a US-format CSV import. Budget
~25-35 minutes for the whole list.

Each item has a short "What changed", numbered steps, and checkbox expected
results. Tick as you go; anything that fails is a release blocker to note (open
issues go into the private backlog tracker).

## Setup

1. Open the app at http://127.0.0.1:8099 (the local demo). The demo ships with
   sample transactions, categories, budgets, projects and savings, so most
   screens have data to work with already.
2. AI features (the "AI categorise" panels and the per-row "suggest") need an AI
   key in **Settings -> AI & privacy** sub-tab (env `HAFI_AI_API_KEY`, or set one
   from the UI on a standalone instance). If AI is off, skip the AI steps or
   configure a key first.
3. **Navigation is now grouped.** The sidebar shows groups (Money, Library,
   Wealth, Plans, System) plus standalone Dashboard / Search / Energy; open a
   group to land on its first page, then use the **sub-tabs** at the top of the
   page to reach the others. So e.g. Transactions is under **Money**, Categories
   under **Library**, and **Settings** under **System** (and Settings itself is
   split into General / AI & privacy / Security / Integrations / Data sub-tabs).
   The steps below name the destination page; reach it via its group.
4. Note: the app is a single-page app. If a screen looks stale after an action,
   it should refresh on its own; a manual reload should never be required. (After
   an *update*, an open tab auto-reloads once to pick up new assets.)

- [ ] App loads at http://127.0.0.1:8099 with sample data visible on the Dashboard.

## Grouped navigation and the Customise editor

**What changed:** the sidebar's pages are organised into **groups** (Money,
Library, Wealth, Plans, System, plus standalone Dashboard / Search / Energy).
Opening a group lands on its first page and shows **sub-tabs** across the top to
switch between the group's pages. A **Customise navigation** editor (sidebar
footer) lets you rename / hide / reorder pages, move them between groups, and
create your own groups; the layout is saved per user (server-side).

1. In the sidebar, click a group header (e.g. **Library**) and use the sub-tabs
   at the top to switch between Categories / Tags / Vendors / Rules.
2. Click **Customise navigation** in the sidebar footer. Rename a group, hide a
   page, drag (or use ▲▼ / "Move to…") to reorder and to move a page into another
   group, then create a new group and drop a page into it.
3. Reload the page, then click **Reset to default** in the editor.

- [ ] Group headers open their first page; the sub-tab strip switches pages within
      the group; standalone pages (Dashboard/Search/Energy) show no sub-tabs.
- [ ] Editor changes (rename / hide / reorder / move / new group) apply to the
      sidebar immediately and **survive a reload** (saved per user, not per device).
- [ ] Hidden pages disappear from the sidebar but remain listed in the editor to
      re-show; **Reset to default** restores the original layout.

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

## Transactions: export filtered set / selected rows to CSV

**What changed:** the toolbar Export CSV button downloads the *filtered* set (not
just the current page); if you have rows ticked, it exports **only the selected
rows** instead.

1. On **Transactions**, apply a filter (a date quick-range or a search term) with
   nothing ticked, and click **Export CSV** (the down-arrow button near the top).
2. Now tick a few specific rows and click **Export CSV** again.

- [ ] With nothing selected, `transactions.csv` contains all filtered rows (not
      only the visible page).
- [ ] With rows ticked, the export contains exactly those selected rows.

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
on your privacy mode (Settings -> AI & privacy sub-tab). They differ by design:

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

**Local panel** (set Settings -> AI & privacy to `local_llm`; click `✨ AI categorise…`):
- [ ] The header checkbox selects/clears every suggestion (indeterminate when some).
- [ ] Export CSV downloads `ai-suggestions.csv` (description, suggested category, confidence).

**Cloud panel** (`cloud_manual`; click `☁️ AI categorise (cloud)…`):
- [ ] The "Will send" list shows only a redacted description + amount per row.
- [ ] Export CSV of the will-send list (and of the suggestions after Send) works.
- [ ] **Send to cloud** returns immediately and the panel shows live "Sent X / N"
      progress that climbs as items complete (the send runs in the background - the
      UI is not frozen while it works). Pressing Send twice does not double-send.
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

**What changed:** every budget shows one clear prorated "pace" line - **over pace**
(spending faster than the period has elapsed), **under pace** (slower) or **on
pace** - alongside the over/near-limit/on-track status. (Earlier builds hid the
line on over-budget rows and used confusing "ahead/behind" wording; it now shows
on all budgets with direction-explicit labels.)

1. Go to **Budgets** (or the Budgets card on the Dashboard).
2. Read the status and pace line on each budget.

- [ ] Every budget shows a single pace label: "over pace" / "under pace" / "on pace".
- [ ] An over-budget budget reads e.g. "over budget · over pace" with no contradictory wording,
      and the pace line is present (no longer hidden).

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
4. Add a new category and note the colour it is pre-filled with.

- [ ] Delete / colour / select controls have descriptive labels.
- [ ] Merge re-points the source's transactions onto the target and removes the source.
- [ ] Bulk recolour applies to every ticked category; Dashboard swatches update.
- [ ] A new category defaults to an unused colour (not a clash with an existing one).

## Global search: keyboard navigation

**What changed:** the global search is keyboard-navigable and its result groups
show clickable category/vendor chips that deep-link into filtered transactions.
The keyboard highlight/hint now stays hidden until you actually press an arrow
key, so mouse users are not distracted by it.

1. Open **Search**. Type at least two characters and use the mouse only.
2. Now press Down/Up to move the highlight, then Enter to open the highlighted result.
3. Back on Search, click a category or vendor chip.

- [ ] Before any arrow key, there is no distracting highlight/hint for mouse users.
- [ ] Arrow keys reveal and move a visible highlight; Enter opens the highlighted result.
- [ ] A category/vendor chip navigates to Transactions filtered by that entity.

## Settings: MFA backup codes

**What changed:** with MFA enabled, the **Settings -> Security** sub-tab shows a
backup-codes section to generate, copy and download one-time recovery codes.

> To test this, first enable MFA for your user on the **Settings -> Security**
> sub-tab (scan the TOTP QR with an authenticator app and confirm a code). The
> backup-codes section only appears once MFA is enabled. Skip this section if not
> testing MFA.

1. Go to **Settings -> Security** sub-tab. Read the "N unused backup codes remaining" line.
2. Generate a new set, then copy to clipboard and download the `.txt`.

- [ ] The section reports how many unused backup codes remain.
- [ ] Generating shows a fresh set with a "save them now" warning.
- [ ] Copy-to-clipboard and download `hafi-backup-codes.txt` both work.

## Edit after creation (projects, budgets, rules, savings goals)

**What changed:** projects, budgets, rules and savings goals can now be **edited**
after they are created (previously delete-and-recreate); a savings account's
name/institution can also be edited (its currency stays read-only).

1. Go to **Projects**, open a project, and edit a field (e.g. its budget or name); save.
2. Go to **Budgets**, edit an existing budget's limit/period; save.
3. Go to **Rules**, edit an existing rule's condition or action; save.
4. Go to **Savings**, edit a goal's target/date, and edit an account's
   name/institution; save.

- [ ] Each of projects, budgets, rules and savings goals has an edit affordance
      and the change persists after refresh.
- [ ] A savings account's name/institution can be edited; its currency cannot.

## Tags: dedicated page

**What changed:** tag management moved out of Settings into its own **Tags** page
(now under the **Library** group, alongside Categories / Vendors / Rules);
"Manage tags" links point there.

1. Open the **Library** group and pick the **Tags** sub-tab (or click a "Manage tags" link).

- [ ] Tags open on their own `/tags` page (not a Settings card), showing usage
      counts, merge and cleanup of unused tags.

## Settings: app-wide date format

**What changed:** a toggle on the **Settings -> General** sub-tab picks how dates
are shown everywhere - ISO (`2026-07-18`), US (`07/18/2026`) or UK (`18/07/2026`).

1. Go to **Settings -> General** sub-tab, find the date-format toggle, and switch it (e.g. to US).
2. Visit **Transactions** and a couple of other pages (Dashboard, Travel).

- [ ] Changing the format updates displayed dates consistently across pages.
- [ ] The choice persists after a reload.

## Security-event MQTT notifications (add-on / config option)

**What changed:** an opt-in option publishes security events (failed unlock,
failed two-factor, wrong passphrase) to MQTT so Home Assistant can alert on them.
It is an **add-on / environment configuration** option (`mqtt_security_events`,
default off) - the same place MQTT itself is enabled - **not** a runtime Settings
toggle. There is nothing to click in the UI for this one.

> Config-driven and only meaningful with MQTT enabled. To exercise it: set
> `mqtt_security_events: true` (add-on Configuration tab) or `HAFI_MQTT_SECURITY_EVENTS=1`
> (standalone env), restart, then trigger a failed unlock / wrong passphrase and
> watch for the event on the broker. If you are not testing MQTT, skip this section.

- [ ] With `mqtt_security_events` enabled + MQTT on, a failed unlock / failed MFA /
      wrong passphrase publishes a security event to the broker.
- [ ] It defaults to **off** (no events published unless explicitly enabled).

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
