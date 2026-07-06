# Rules

Rules automate categorisation and tidy-up. Each rule is a **condition → action**
pair: when a transaction matches the condition, the action is applied. Rules run
**during import** and whenever you hit **Re-categorise**.

The Rules page has a built-in **"How rules work"** panel with the same reference
and live examples; this document is the standalone version.

## The app learns — less manual work *and* less AI over time

Categorisation runs in a fixed order: **manual → learned rules → vendor defaults →
category keyword library → (opt-in) AI**. AI is only ever the **last resort** for
what the first four didn't already settle. That ordering is what makes the app get
*quieter* the more you use it: anything you teach it is reused automatically on
future imports, so fewer transactions need a manual touch — and fewer ever reach
the AI.

You teach it three ways, all of which persist and apply to **future imports** (and
to existing rows when you hit **Re-categorise**):

- **Learn a rule from a correction.** When you set a category on a transaction, use
  **"+ rule"** to turn "descriptions like this → this category" into a saved rule.
  It's **idempotent** — teaching the same merchant→category twice reuses the one
  rule, it never piles up duplicates. From then on, matching transactions are
  categorised with **zero** AI and zero clicks.
- **Build the vendor library.** Assigning (or creating) a **vendor** on a
  transaction adds a *contains* alias and, if you give the vendor a **default
  category**, every future transaction from that merchant is recognised and
  categorised automatically.
- **Grow the category keyword library.** The built-in keyword fallback catches
  common descriptions; adding your own categories extends it.

**Why this also lowers AI usage (and cost, and data exposure).** Because AI sits
*after* rules/vendors/keywords, a transaction only goes to the AI if none of those
matched. As your rules and vendor defaults accumulate, the share of each new
statement that's already settled keeps rising — so next month's import is mostly
auto-categorised from what you taught it this month, and the AI (if you've enabled
it at all) is consulted for less and less. Less manual review, fewer cloud calls.
See [privacy.md](privacy.md) for the privacy side of that.

## How rules are applied (precedence)

- Only **enabled** rules are considered.
- Rules run in **priority order, highest priority first** (then by id). A rule's
  `priority` defaults to `100`; raise it to make a rule win over others.
- **For each action type, the first matching rule wins.** Once a `set_category`
  rule has fired, a lower-priority `set_category` rule won't override it — but a
  rule with a *different* action (e.g. `set_project`) can still fire on the same
  transaction.
- So a transaction can be touched by several rules (one per action type), each
  the highest-priority match for its action.

This means: put your **specific** rules at a **higher priority** than broad
catch-alls, so the specific one wins.

## Conditions

| Condition | Matches when… | `condition_value` example |
|-----------|---------------|---------------------------|
| `merchant_contains` | the cleaned merchant name contains the text (case-insensitive) | `tesco` |
| `description_contains` | the raw description contains the text | `tfl travel` |
| `vendor_equals` | the transaction's resolved vendor id equals | `42` |
| `account_equals` | the transaction's account id equals | `3` |
| `amount_equals` | the amount equals exactly | `9.99` |
| `amount_between` | the amount is within an inclusive range | `10,50` (min,max — comma- or pipe-separated; use `.` for decimals, e.g. `10.50`; out-of-order bounds are auto-swapped, malformed values simply never match) |
| `category_equals` | the current category id equals (re-route an existing category) | `7` |
| `recurring_payment` | _designed, not yet wired — currently never matches_ | `true` |
| `source_format` | _designed, not yet wired — currently never matches_ | `monzo_csv` |

## Actions

| Action | Effect | `action_value` |
|--------|--------|----------------|
| `set_vendor` | assign a vendor | vendor id |
| `set_category` | assign a category | category id |
| `set_project` | assign a project | project id |
| `set_country` | tag the spend location (for the spend-by-location map) | ISO alpha-2, e.g. `ES` |
| `mark_transfer` | flag as a transfer (excluded from spend/income) | — |
| `mark_income` | flag as income | — |
| `require_review` | send to the review queue | — |
| `mark_subscription` | record the transaction as a subscription (creates a *possible* subscription for its vendor/name, which the recurring-payment detector later confirms) | — |
| `block_cloud_ai` | _designed, not yet wired — use per-category privacy instead (see below)_ | — |

## Worked examples

**1 — Categorise a supermarket.**
- Condition: `merchant_contains` = `tesco`
- Action: `set_category` = *Groceries*
- Priority: `100`

**2 — A specific rule beating a broad one.**
- Broad: `description_contains` = `amazon` → `set_category` *Shopping*, priority `100`.
- Specific: `description_contains` = `amazon prime` → `set_category` *Subscriptions*,
  priority `200`.
- Because the specific rule has higher priority, "AMAZON PRIME" rows become
  *Subscriptions*; other Amazon rows fall through to *Shopping*.

**3 — Treat credit-card payments as transfers.**
- Condition: `description_contains` = `payment thank you`
- Action: `mark_transfer`
- Keeps internal money movement out of your spend/income totals.

**4 — Salary as income.**
- Condition: `amount_between` = `2000,5000` (or `description_contains` = `acme payroll`)
- Action: `mark_income`

**5 — Keep a sensitive category off cloud AI.**
- The `block_cloud_ai` action above is designed but not yet wired, so use
  **per-category privacy** instead: mark the category (e.g. *Health*) as
  **never-cloud** in Categories → Cloud-AI privacy.
- Transactions in a never-cloud category are never included in any cloud AI
  payload, regardless of the global AI mode (see [privacy.md](privacy.md)).

**6 — Flag a recurring charge as a subscription.**
- Condition: `merchant_contains` = `netflix`
- Action: `mark_subscription`
- Records the transaction as a *possible* subscription (keyed on its vendor, or
  its merchant name when no vendor is matched). The recurring-payment detector
  then confirms it — promoting it to *active* with a real interval — once it sees
  enough occurrences. Re-running is idempotent: it won't create a duplicate
  subscription for a vendor/name that already has one.

**7 — Tag a foreign vendor's country.**
- Condition: `description_contains` = `mercadona`
- Action: `set_country` = `ES`
- The spend-by-location map then credits **Spain** instead of falling back to the
  coarse currency guess (EUR → "Eurozone"). The code is normalised to two
  upper-case letters. Per-transaction and per-vendor country overrides do the same
  thing by hand; this automates it on import.

## Tips

- **Test before relying on it.** The Rules page lets you try a rule against your
  existing transactions before saving.
- **Vendor defaults vs rules.** Setting a default category on a vendor (Vendors
  page) is often simpler than a rule for "this shop is always this category".
- **Re-categorise** re-applies all rules to existing rows — handy after adding or
  reordering rules.
