# Rules

Rules automate categorisation and tidy-up. Each rule is a **condition → action**
pair: when a transaction matches the condition, the action is applied. Rules run
**during import** and whenever you hit **Re-categorise**.

The Rules page has a built-in **"How rules work"** panel with the same reference
and live examples; this document is the standalone version.

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
| `amount_between` | the amount is within an inclusive range | `10:50` (min:max) |
| `recurring_payment` | the transaction looks like a detected recurring payment | `true` |
| `category_equals` | the current category id equals (re-route an existing category) | `7` |
| `source_format` | the importing parser/format matches | `monzo_csv` |

## Actions

| Action | Effect | `action_value` |
|--------|--------|----------------|
| `set_vendor` | assign a vendor | vendor id |
| `set_category` | assign a category | category id |
| `set_project` | assign a project | project id |
| `set_country` | tag the spend location (for the spend-by-location map) | ISO alpha-2, e.g. `ES` |
| `mark_transfer` | flag as a transfer (excluded from spend/income) | — |
| `mark_income` | flag as income | — |
| `mark_subscription` | flag as a subscription | — |
| `require_review` | send to the review queue | — |
| `block_cloud_ai` | never send this transaction to cloud AI | — |

## Worked examples

**1 — Categorise a supermarket.**
- Condition: `merchant_contains` = `tesco`
- Action: `set_category` = *Groceries*
- Priority: `100`

**2 — A specific rule beating a broad one.**
- Broad: `description_contains` = `amazon` → `set_category` *Shopping*, priority `100`.
- Specific: `description_contains` = `amazon prime` → `set_category` *Subscriptions*
  **and** `mark_subscription`, priority `200`.
- Because the specific rule has higher priority, "AMAZON PRIME" rows become
  *Subscriptions*; other Amazon rows fall through to *Shopping*.

**3 — Treat credit-card payments as transfers.**
- Condition: `description_contains` = `payment thank you`
- Action: `mark_transfer`
- Keeps internal money movement out of your spend/income totals.

**4 — Salary as income.**
- Condition: `amount_between` = `2000:5000` (or `description_contains` = `acme payroll`)
- Action: `mark_income`

**5 — Keep a sensitive merchant off cloud AI.**
- Condition: `merchant_contains` = `clinic`
- Action: `block_cloud_ai`
- That transaction is never included in any cloud AI payload, regardless of the
  global AI mode. (Category-level privacy does the same per category — see
  [privacy.md](privacy.md).)

**6 — Tag a foreign vendor's country.**
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
