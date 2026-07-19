# Roadmap

Where **HA Finance Intelligence** is heading. This is a direction, not a set of
dated promises - items can move, merge, or drop, and nothing here is guaranteed.
For what has actually shipped, see the [CHANGELOG](CHANGELOG.md).

Everything below stays true to the project's principles: **local-first,
opt-in for anything that leaves your device, and auditable**. See
[Principles](#principles-we-wont-break) at the bottom.

Have an idea or a strong opinion on priorities? Please open a
[GitHub issue or discussion](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/issues) -
this roadmap is meant to be shaped by the people who use it.

---

## Here today

The app already imports statements, categorises transactions (rules + a
vendor/category library), splits charges, tracks projects, budgets,
subscriptions, savings, investments and assets, scans receipts with optional
local OCR, handles multiple currencies, and publishes finance sensors to Home
Assistant over MQTT - with optional, opt-in local or cloud AI for category
suggestions. Recent releases added forecasts (budget pace, project burn-down,
savings time-to-goal), a customisable grouped sidebar, two-factor backup codes,
and a hardened, non-root container. The [CHANGELOG](CHANGELOG.md) has the detail.

## Next up

Near-term improvements we intend to build on top of what's here:

- **Faster corrections** - undo for bulk edits, choosing which category survives
  a merge, and re-running rules/matches on an already-imported statement.
- **Smarter categorisation** - weighted and per-household keyword rules, vendor
  aliases that learn from your corrections, and rule regex conditions.
- **Richer analytics** - "this recurring charge changed amount" alerts,
  per-category trend sparklines, and a multi-sheet spreadsheet export.
- **Wealth depth** - realised gains / cost basis and dividends for investments,
  subscription price-change detection with annualised totals, and cost-per-mile
  for cars.
- **Import breadth** - more bank formats and cleaner column mapping.

## Exploring - the bigger picture

The larger bets that define where the app goes next. The thesis: today the app
**records and explains the past**; next it should help you **plan and automate
the future**, natively in your home.

### Net worth and liabilities
Assets are tracked, but debt isn't yet. Add loan / mortgage / credit-card /
finance account types with balances, APR and terms - then a single **Net Worth**
view and over-time chart (assets minus liabilities), a debt payoff planner
(snowball vs avalanche), and a net-worth sensor for Home Assistant.

### Cash-flow forecasting and a bill calendar
Project your balance forward from recurring income, detected subscriptions/bills
and budgeted spend - "your current account dips to a low on the 28th before
payday" - plus a due-date bill calendar that can surface as a Home Assistant
calendar.

### A local AI financial assistant
The privacy-preserving AI groundwork is already here (an audited gateway, a
redaction choke-point, local-LLM support, per-call privacy modes). The next step
is conversational Q&A over **your own data**, fully on-device in local mode:
natural-language rule creation, a proactive weekly-insights feed, and
duplicate-charge / anomaly detection.

### Deeper, two-way Home Assistant integration
Go beyond one-way sensors: fire HA events on budget-exceeded, a large charge, or
a bill due; expose Voice/Assist intents; ship Lovelace cards; and integrate with
HA Calendar (bills), To-do (the review queue) and notify entities.

### Reports, tax and broader ingestion
Configurable tax year, PDF reports and deductibility flags with a
self-assessment / Schedule-C style export; automated ingestion (a watched
folder, IMAP statement pull, and **opt-in** Open Banking); more file formats
(OFX/QIF/MT940/CAMT, broker statements, read-only crypto); expense-splitting and
settle-up; and envelope / zero-based budgeting with sinking funds.

## Smaller improvements we'd like to make

- Rule test/preview ("this would match 42 transactions") + bulk-apply
- Merchant logos on the Vendors page
- A spending heatmap and a comparison mode (vs last month / same month last year)
- Budget rollover and savings auto-linked from transfer transactions
- A shareable, read-only, time-boxed snapshot for an accountant
- An installable PWA with mobile-first receipt capture

## For very large households / performance

The app is fast for a normal household (thousands of rows). At extreme scale
(hundreds of thousands to millions of rows) some dashboard aggregates slow down;
the path there is better indexes, short-circuiting, and - eventually - an
optional PostgreSQL backend with pre-computed monthly rollups.

## Principles we won't break

- **No default cloud SaaS.** The app runs entirely on your hardware. Cloud AI is
  strictly opt-in, per-call, redacted and auditable.
- **No ads, ever**, and no selling or sharing of your data.
- **No always-on bank scraping by default.** Any account connection (including
  Open Banking, if it lands) is opt-in and under your control.
- **Local-first and auditable.** You can see what the app did and why, and turn
  off anything you don't want.

---

_This roadmap is directional and will change. Dates and ordering are not
commitments. Want to influence it? Open an
[issue or discussion](https://github.com/SpiritSLO-UK/HomeAssistant-expenses-analytics/issues)._
