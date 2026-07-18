# Screenshots

A tour of **HA Finance Intelligence**, captured on the built-in demo data
(`Settings → Demo data → Load demo data`). All amounts/names here are fabricated
demo data. AI is off in these shots - everything you see is processed locally.

## Dashboard

The dashboard is a stack of toggleable cards you can reorder. Top to bottom:

![Dashboard - quick-add, monthly totals, needs-attention and a heads-up feed](screenshots/capture-dashboard-1.PNG)

> Quick-add (receipt / photo / import), this month's **spend / income / net**, a
> per-member filter, items needing attention, and a **heads-up feed** - unusually
> large charges and upcoming subscription renewals.

![Dashboard - trends, spending by category, top vendors and a location map](screenshots/capture-dashboard-2.PNG)

> Six-month **trends**, **spending by category**, **top vendors**, and a
> **spend-by-location** map (every row and point drills through to its transactions).

![Dashboard - location, projects, savings and car/home costs](screenshots/capture-dashboard-3.PNG)

> Spend by location, **by project**, **savings** (total + goal progress) and
> **car / home** running costs.

![Dashboard - budgets, business, travel, allowance and processing stats](screenshots/capture-dashboard-4.PNG)

> **Budgets**, **business/VAT**, **travel** spend-abroad, **allowance**, and a
> **processing** summary - here showing every enrichment done locally, *no AI calls*.

## Transactions & import

![Transactions - filters and an expanded row editor](screenshots/capture-transactions-1.PNG)

> Filter by date, category, vendor, project, member or free text; expand any row to
> edit its **category** (with a rule or ✨ AI suggestion), **vendor**, **country**,
> **tags**, **business/VAT**, **splits**, and attach a **receipt**.

![Import - statements and receipts, with a review step](screenshots/capture-import-1.PNG)

> Import a bank statement (**CSV / PDF / photo**) or upload a **receipt**. Imports go
> through a review step before anything is saved.

## Receipts

![Receipts - OCR extraction and transaction matching](screenshots/capture-receipts-1.PNG)

> Upload or photograph a receipt; **local OCR** reads the merchant, date and total,
> then matches it to a candidate transaction - or create a transaction straight from
> the receipt.

## Search

![Global search across transactions, vendors, categories and projects](screenshots/capture-search-1.PNG)

> One box searches transactions, vendors, categories and projects - instant even on
> very large datasets (backed by a full-text index).

## Budgets, investments & categories

![Budgets - per-category limits with progress](screenshots/capture-budgets-1.PNG)

> Per-category **budgets** with progress, drill-down to the transactions behind each,
> and an annualised "this year" view.

![Investments & pensions - holdings, value over time and change](screenshots/capture-investments-1.PNG)

> **Investments & pensions** - holdings with market value and unrealised gain, plus
> **value-over-time** and day / month / year change.

![Categories - library, cloud-AI privacy levels and merge](screenshots/capture-categories-1.PNG)

> Your **category library**, per-category **cloud-AI privacy** levels (cloud OK ·
> sensitive · never-cloud), and category **merge**.

## Settings (local-first & private)

![Settings - appearance, status, storage & statistics, services](screenshots/capture-settings-1.PNG)

> Theme, status, a **Storage & statistics** card (database size + AI-call tallies),
> and the **Services** panel - AI, receipt OCR, online exchange rates and MQTT.

![Settings - currency and exchange rates](screenshots/capture-settings-2.PNG)

> Base currency and **exchange rates** (manual or online via Frankfurter), plus the
> spend-by-location default country.

![Settings - MQTT sensors, Paperless import and the AI assistant](screenshots/capture-settings-3.PNG)

> **Home Assistant MQTT sensors**, optional **Paperless-ngx** import, and the **AI
> assistant** - off by default; works with any OpenAI-compatible (local or cloud) endpoint.

![Settings - two-factor, security health, encryption and retention](screenshots/capture-settings-4.PNG)

> **Two-factor (MFA)**, **security health** checks, **at-rest database encryption**,
> and **data retention** (archive / purge per type).

![Settings - logging, demo data, backups and config export](screenshots/capture-settings-5.PNG)

> Logging, demo data, **backup & restore** (including encrypted backups) and
> config/library export - your data never leaves the device.

## Logs (audit trail)

![Logs - audit trail with AI & privacy decisions grouped](screenshots/capture-logs-1.PNG)

> A record of important actions, with **AI & privacy decisions** grouped separately
> from routine activity for a clear consent trail.

## Users & access

![Users - roles, page access and two-factor](screenshots/capture-users-1.PNG)

> A **multi-user household**: approve people, set roles (owner / member / viewer /
> child), restrict which pages each can reach, and require two-factor.
