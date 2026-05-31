# Home Assistant Personal Finance App — Full Product, Architecture and Build Specification

**Working name:** HA Finance Intelligence  
**Primary target:** Home Assistant add-on / app  
**Default operating model:** Strictly local, privacy-first  
**Long-term direction:** Personal-first project that can mature into an open-source project  
**Core concept:** A Home Assistant-first personal finance application that imports bank statements, categorises transactions, supports split transactions, builds a category/vendor library, tracks projects and budgets, uploads receipts, performs local OCR, and optionally uses local or cloud AI to classify, enrich and reconcile financial data.

---

## 1. Vision

Build a **full personal finance app for Home Assistant**.

The app should help a household understand, categorise, review and automate personal finances using a local-first architecture.

It should support:

- CSV bank imports first.
- PDF bank statement imports later.
- Receipt upload and OCR later.
- Local categorisation rules.
- User-editable category library.
- Split transactions.
- Projects/tags.
- Optional household and partner support.
- Home Assistant dashboards, sensors, alerts and automations.
- Strict local mode by default.
- Optional local LLM mode.
- Optional cloud AI mode with manual or automatic approval.
- A path towards open-source reuse.

The app should not start as a SaaS, nor should it require cloud accounts, Open Banking, or external AI to be useful.

---

## 2. Product Positioning

### 2.1 One-line description

A local-first Home Assistant personal finance app for importing, categorising, enriching, reviewing and automating household financial data.

### 2.2 Longer description

HA Finance Intelligence is a Home Assistant-first personal finance platform. It imports bank statements from Curve, Barclays, Lloyds, Monzo and other providers, normalises transactions, categorises spending, supports split transactions, builds a vendor and category knowledge base, tracks household projects, monitors subscriptions, scans receipts using local OCR, and optionally uses local or cloud AI to classify vendors, parse receipts and resolve uncertain transactions.

### 2.3 What makes it different

Existing finance apps are usually cloud-first, budgeting-first, or standalone. This project is:

- **Home Assistant-first**: native dashboard, sidebar panel, sensors, notifications and automations.
- **Local-first**: no cloud required.
- **AI-optional**: rules work first; AI helps only where useful.
- **Receipt-aware**: receipts can enrich transactions rather than being separate documents.
- **Category-library-driven**: categories become a reusable data asset.
- **Household-aware**: supports personal, shared and project-based finance.

---

## 3. Goals

### 3.1 Primary goals

1. Import bank statement CSV files.
2. Normalise transactions into one standard format.
3. Categorise transactions using rules and a category library.
4. Allow manual corrections and learn from them.
5. Support split transactions.
6. Support projects and tags.
7. Provide Home Assistant dashboards and sensors.
8. Provide a review queue for unknown, duplicate or low-confidence transactions.
9. Support strict local operation by default.
10. Prepare the architecture for OCR and AI later.

### 3.2 Secondary goals

1. Parse PDF bank statements.
2. Upload receipts.
3. Run local OCR.
4. Match receipts to transactions.
5. Support optional item-level receipt extraction.
6. Support local LLMs.
7. Support cloud AI with approval workflows.
8. Detect subscriptions and recurring payments.
9. Support budgets and alerts.
10. Support household/partner finance modes.

### 3.3 Long-term goals

1. Home Assistant add-on repository.
2. Open-source-ready documentation.
3. Bank parser contribution guide.
4. Category library contribution guide.
5. Optional standalone Docker Compose deployment.
6. Optional Grafana integration.
7. Optional Open Banking integration.

---

## 4. Non-goals for MVP

The MVP should **not** include:

- Open Banking.
- Cloud AI as a requirement.
- PDF parsing.
- Receipt OCR.
- Mobile app.
- Investment tracking.
- Tax/accounting reports.
- Full double-entry accounting.
- SaaS multi-tenant hosting.
- Advanced forecasting.
- Item-level receipt categorisation.

These can come later.

---

## 5. Target User

### 5.1 Initial user

The first user is a technical Home Assistant user who wants to manage household spending locally.

They likely have:

- Home Assistant OS or supervised installation.
- A small server, mini PC, NAS, NUC, Raspberry Pi, or homelab machine.
- Multiple bank/card providers.
- CSV or PDF bank exports.
- A desire for privacy and control.
- Interest in automation, dashboards and AI.

### 5.2 Future open-source users

Future users may include:

- Home Assistant users.
- Self-hosted enthusiasts.
- Privacy-focused users.
- People who want local receipt OCR.
- People who want AI-assisted transaction categorisation.
- People managing home renovation, projects or shared household spending.

---

## 6. Operating Modes

The app should support multiple setup modes.

### 6.1 Personal mode

One user, personal accounts only.

Use case:

- Personal current account.
- Personal credit card.
- Personal subscriptions.
- Personal spending dashboard.

### 6.2 Household mode

Multiple users in one household.

Use case:

- Blaz.
- Andreia.
- Joint expenses.
- Shared home costs.
- Shared projects.
- Optional private accounts.

### 6.3 Household with shared/private accounts

Some accounts are shared, some are private.

Example:

- Joint account: mortgage, utilities, groceries.
- Blaz personal account.
- Andreia personal account.
- Shared projects.
- Personal spending hidden or separated.

### 6.4 Project-heavy mode

House renovation, moving, travel, car or DIY projects are core.

Example projects:

- House purchase.
- Moving.
- Bathroom renovation.
- Kitchen renovation.
- Garden.
- Smart home.
- Tools.
- Holiday.
- Car maintenance.

---

## 7. Privacy Modes

Privacy mode is a first-class product setting.

### 7.1 Strict Local Mode

Default mode.

Behaviour:

- No cloud AI calls.
- No external API calls.
- No telemetry.
- No external vendor lookup.
- Local rules only.
- Local OCR only when OCR exists.
- Local LLM disabled unless separately enabled.
- All data remains inside the Home Assistant environment.

This must be the default.

### 7.2 Local LLM Mode

Behaviour:

- Local OCR.
- Local LLM for merchant cleanup, category suggestion and receipt parsing.
- Supported through Ollama or OpenAI-compatible local endpoints.
- No cloud AI unless separately enabled.

### 7.3 Cloud AI Manual Approval Mode

Behaviour:

- Local processing first.
- Cloud AI is only used for selected uncertain cases.
- User must approve each external request.
- Payload is redacted before sending.
- Sensitive transactions are blocked by default.

### 7.4 Cloud AI Automatic Approval Mode

Behaviour:

- Local processing first.
- Cloud AI can be used automatically for low-risk, redacted tasks.
- Sensitive categories still require manual approval or remain local-only.
- Every external request is logged.

### 7.5 No-AI Mode

Behaviour:

- Rules, manual categorisation and import only.
- No local LLM.
- No cloud AI.
- Useful for users who do not want AI at all.

---

## 8. Home Assistant-First Requirements

The project is Home Assistant-first.

The first supported deployment should be a Home Assistant add-on/app with an integrated sidebar UI.

### 8.1 Home Assistant add-on/app

The add-on should provide:

- Backend API.
- Web UI.
- Database access.
- Import processing.
- Optional worker.
- Optional OCR worker later.
- Optional MQTT publishing.

### 8.2 Sidebar UI

The web UI should be available from the Home Assistant sidebar using ingress.

Home Assistant ingress allows an add-on UI to appear inside the Home Assistant interface and lets Home Assistant handle authentication and secure access.

Implementation implications:

- The app must serve a web UI on an internal port.
- The Home Assistant add-on config should enable ingress.
- The app should trust Home Assistant ingress authentication rather than implementing a separate login for MVP.
- The app should still implement internal permission boundaries later.

### 8.3 HA sensors

The app should expose finance summary data as Home Assistant sensors.

Initial sensors:

- `sensor.finance_spend_this_month`
- `sensor.finance_income_this_month`
- `sensor.finance_net_this_month`
- `sensor.finance_groceries_this_month`
- `sensor.finance_bills_this_month`
- `sensor.finance_home_this_month`
- `sensor.finance_diy_this_month`
- `sensor.finance_subscriptions_total`
- `sensor.finance_review_items`
- `sensor.finance_unknown_transactions`
- `sensor.finance_unmatched_receipts`
- `sensor.finance_project_house_total`
- `sensor.finance_cashflow_forecast`

### 8.4 MQTT first

For MVP, MQTT is the simplest and most Home Assistant-native integration path.

The app should publish state topics and discovery payloads.

Example state topic:

```text
homeassistant/finance/state/spend_this_month
```

Example discovery topic:

```text
homeassistant/sensor/finance_spend_this_month/config
```

Example discovery payload:

```json
{
  "name": "Finance Spend This Month",
  "unique_id": "finance_spend_this_month",
  "state_topic": "homeassistant/finance/state/spend_this_month",
  "unit_of_measurement": "GBP",
  "device_class": "monetary",
  "state_class": "measurement",
  "icon": "mdi:cash",
  "device": {
    "identifiers": ["ha_finance_intelligence"],
    "name": "HA Finance Intelligence",
    "manufacturer": "HA Finance Intelligence"
  }
}
```

### 8.5 Home Assistant notifications

The app should create events or publish data that can trigger notifications.

Examples:

- New unknown vendor found.
- Possible subscription detected.
- Monthly budget exceeded.
- Receipt uploaded but not matched.
- Transaction needs review.
- Cloud AI approval required.
- Duplicate transaction suspected.
- Mortgage/bill amount changed.

### 8.6 Home Assistant automations

Example automations:

- Notify when groceries exceed budget.
- Notify when DIY project exceeds planned budget.
- Notify when a new subscription appears.
- Notify when a receipt is uploaded but unmatched.
- Notify at month end with spending summary.
- Turn dashboard entity red/alerting if review queue > 0.

---

## 9. System Architecture

### 9.1 Recommended architecture

```text
Home Assistant
  ├── Sidebar Panel via Ingress
  ├── MQTT Sensors
  ├── Notifications
  └── Automations

HA Finance Add-on
  ├── Frontend UI
  ├── FastAPI Backend
  ├── Worker Queue
  ├── Import Engine
  ├── Category Engine
  ├── Rule Engine
  ├── Vendor Engine
  ├── Matching Engine
  ├── OCR Engine later
  ├── AI Gateway later
  └── PostgreSQL or SQLite database
```

### 9.2 MVP architecture

For MVP, keep it simpler:

```text
Home Assistant Add-on
  ├── FastAPI backend
  ├── React frontend
  ├── SQLite database
  ├── CSV import engine
  ├── category/rule engine
  ├── MQTT publisher
  └── background job runner
```

PostgreSQL can be added later, but SQLite is simpler inside a Home Assistant add-on.

### 9.3 Future architecture

Later, when more complex:

```text
Home Assistant Add-on
  ├── API container
  ├── UI container
  ├── Worker container
  ├── OCR container
  ├── PostgreSQL container
  ├── Redis container
  └── Optional Ollama/local LLM endpoint
```

### 9.4 Key architectural principle

The Home Assistant integration should not contain business logic.

Business logic lives in the finance backend:

- Importing.
- Normalising.
- Categorising.
- Matching.
- AI calls.
- Receipt parsing.
- Rule learning.
- Budget calculations.

Home Assistant receives data, displays dashboards, and triggers automations.

---

## 10. Suggested Tech Stack

### 10.1 Backend

Recommended:

- Python.
- FastAPI.
- SQLAlchemy.
- Alembic.
- Pydantic.
- SQLite for MVP.
- PostgreSQL later.
- APScheduler or lightweight background jobs for MVP.
- RQ/Celery + Redis later if needed.

### 10.2 Frontend

Recommended:

- React.
- TypeScript.
- Vite.
- TanStack Query.
- TanStack Table.
- Recharts.
- Tailwind CSS.
- shadcn/ui or similar component library.

### 10.3 Home Assistant integration

Recommended initial method:

- Add-on/app with ingress.
- MQTT discovery for sensors.
- REST API later.
- Custom integration later only if needed.

### 10.4 OCR later

Candidate tools:

- Tesseract.
- PaddleOCR.
- OCRmyPDF.
- Paperless-ngx integration.
- OpenCV preprocessing.

### 10.5 Local LLM later

Candidate interfaces:

- Ollama.
- LM Studio.
- llama.cpp server.
- Any OpenAI-compatible endpoint.

### 10.6 Cloud AI later

Candidate providers:

- OpenAI-compatible endpoint.
- Anthropic Claude.
- Google Gemini.
- xAI/Grok.
- OpenRouter.
- Azure OpenAI.
- AWS Bedrock.

Implement OpenAI-compatible first because many local and cloud tools expose that API style.

---

## 11. Repository Structure

Recommended initial repository:

```text
ha-finance-intelligence/
  README.md
  LICENSE
  docker-compose.dev.yml
  docs/
    architecture.md
    privacy.md
    category-library.md
    bank-imports.md
    home-assistant.md
    ai-providers.md
    receipt-ocr.md
    development.md

  addon/
    config.yaml
    Dockerfile
    run.sh
    rootfs/

  backend/
    pyproject.toml
    alembic.ini
    app/
      main.py
      config.py
      logging.py
      api/
        routes_imports.py
        routes_transactions.py
        routes_categories.py
        routes_vendors.py
        routes_rules.py
        routes_projects.py
        routes_budgets.py
        routes_review.py
        routes_settings.py
        routes_health.py
      db/
        session.py
        migrations/
      models/
        account.py
        statement.py
        transaction.py
        category.py
        vendor.py
        receipt.py
        rule.py
        project.py
        budget.py
        review_item.py
        ai_request.py
        settings.py
      schemas/
      services/
        import_service.py
        category_service.py
        vendor_service.py
        rule_service.py
        matching_service.py
        budget_service.py
        review_service.py
        mqtt_service.py
      parsers/
        base.py
        curve_csv.py
        barclays_csv.py
        lloyds_csv.py
        monzo_csv.py
        generic_csv.py
      category_library/
        defaults.json
      ai/
        base.py
        no_ai.py
        openai_compatible.py
        ollama.py
      ocr/
        base.py
        local_ocr.py
      tests/

  frontend/
    package.json
    vite.config.ts
    src/
      main.tsx
      App.tsx
      api/
      components/
      pages/
        Dashboard.tsx
        Transactions.tsx
        Import.tsx
        Categories.tsx
        Vendors.tsx
        Rules.tsx
        Projects.tsx
        Budgets.tsx
        ReviewQueue.tsx
        Settings.tsx
      routes/
      styles/

  examples/
    sample-csv/
    sample-category-library/
    sample-mqtt/
```

---

## 12. Data Model

### 12.1 Core entities

The database should support the full future shape from the beginning.

Core entities:

- User.
- Household.
- Account.
- Statement.
- Transaction.
- TransactionSplit.
- Category.
- Vendor.
- VendorAlias.
- Rule.
- Project.
- Tag.
- Budget.
- Receipt.
- ReceiptItem.
- TransactionReceiptMatch.
- ReviewItem.
- AIRequest.
- Setting.
- AuditLog.

### 12.2 Users

Even if MVP is single-user, support users in the model.

Fields:

```text
id
household_id
display_name
email nullable
role
is_active
created_at
updated_at
```

Roles:

- owner
- member
- viewer

### 12.3 Households

Fields:

```text
id
name
currency
mode
created_at
updated_at
```

Modes:

- personal
- household
- shared_private

### 12.4 Accounts

Fields:

```text
id
household_id
owner_user_id nullable
name
institution
account_type
currency
last_four nullable
is_shared
is_active
created_at
updated_at
```

Account types:

- current_account
- credit_card
- savings
- loan
- mortgage
- cash
- other

Example:

```json
{
  "name": "Curve",
  "institution": "Curve",
  "account_type": "credit_card",
  "currency": "GBP",
  "is_shared": false
}
```

### 12.5 Statements

Fields:

```text
id
account_id
source_type
source_format
source_filename
source_hash
period_start
period_end
imported_at
transaction_count
duplicate_count
status
notes
```

Source types:

- manual_upload
- email
- open_banking
- api
- generated

Source formats:

- csv
- pdf
- ofx
- qif
- camt053
- xlsx

### 12.6 Transactions

Fields:

```text
id
household_id
account_id
statement_id nullable
external_id nullable
transaction_date
posted_date nullable
description_raw
merchant_raw nullable
merchant_id nullable
amount
currency
direction
category_id nullable
project_id nullable
is_split
is_transfer
is_income
is_duplicate
needs_review
review_reason nullable
confidence_score
source_hash
created_at
updated_at
```

Direction:

- debit
- credit

### 12.7 Transaction splits

Fields:

```text
id
transaction_id
category_id
project_id nullable
amount
description nullable
notes nullable
created_at
updated_at
```

Rules:

- Split amounts must add up to original transaction amount.
- Splits can have different categories.
- Splits can have different projects.
- Original transaction remains source of truth.

### 12.8 Categories

Fields:

```text
id
household_id nullable
parent_id nullable
library_id nullable
name
path
description
icon
colour
is_system
is_active
is_budgetable
privacy_sensitivity
created_at
updated_at
```

Privacy sensitivity:

- normal
- sensitive
- never_cloud

Examples of sensitive categories:

- salary
- mortgage
- medical
- legal
- insurance
- tax
- loans

### 12.9 Vendors

Fields:

```text
id
household_id nullable
canonical_name
display_name
default_category_id nullable
service_type nullable
website nullable
notes nullable
confidence_score
created_by
last_seen_at
created_at
updated_at
```

### 12.10 Vendor aliases

Fields:

```text
id
vendor_id
alias
source
confidence_score
created_at
updated_at
```

Example aliases:

```text
TESCO STORES 3142 DARTFORD -> Tesco
TESCO PFS -> Tesco Petrol
AMZNMKTPLACE -> Amazon
SCREWFIX DIRECT -> Screwfix
```

### 12.11 Rules

Fields:

```text
id
household_id
name
priority
enabled
condition_type
condition_value
action_type
action_value
created_from
created_at
updated_at
```

Condition types:

- merchant_contains
- description_contains
- vendor_equals
- account_equals
- amount_equals
- amount_between
- recurring_payment
- category_equals
- source_format

Action types:

- set_vendor
- set_category
- set_project
- mark_transfer
- mark_income
- mark_subscription
- require_review
- block_cloud_ai

### 12.12 Projects

Fields:

```text
id
household_id
name
description
status
budget_amount nullable
start_date nullable
end_date nullable
created_at
updated_at
```

Statuses:

- planned
- active
- paused
- complete
- archived

Examples:

- House purchase.
- Moving.
- Bathroom renovation.
- Kitchen renovation.
- Garden.
- Smart home.
- Tools.
- Holiday.

### 12.13 Tags

Fields:

```text
id
household_id
name
colour
created_at
updated_at
```

Many-to-many with transactions.

### 12.14 Budgets

Fields:

```text
id
household_id
category_id nullable
project_id nullable
name
period
amount
currency
start_date
end_date nullable
rollover_enabled
alert_threshold_percent
created_at
updated_at
```

Periods:

- weekly
- monthly
- quarterly
- yearly
- custom

### 12.15 Receipts

Fields:

```text
id
household_id
uploaded_by_user_id
source_filename
file_hash
storage_path
receipt_date nullable
merchant_raw nullable
vendor_id nullable
total_amount nullable
currency nullable
vat_amount nullable
ocr_status
ocr_confidence
needs_review
created_at
updated_at
```

OCR statuses:

- not_processed
- processing
- processed
- failed
- skipped

### 12.16 Receipt items

Optional advanced feature.

Fields:

```text
id
receipt_id
name
quantity nullable
unit_price nullable
total_price
category_id nullable
project_id nullable
confidence_score
created_at
updated_at
```

### 12.17 Transaction-receipt matches

Fields:

```text
id
transaction_id
receipt_id
match_score
match_status
matched_by
created_at
updated_at
```

Match statuses:

- suggested
- confirmed
- rejected
- auto_confirmed

Matched by:

- rule
- local_ocr
- local_ai
- cloud_ai
- user

### 12.18 Review items

Fields:

```text
id
household_id
item_type
item_id
reason
severity
status
suggested_action
created_at
resolved_at nullable
```

Item types:

- transaction
- vendor
- receipt
- category
- ai_request
- import

Reasons:

- unknown_vendor
- unknown_category
- low_confidence
- duplicate_possible
- receipt_unmatched
- split_invalid
- cloud_ai_approval_required
- sensitive_data_detected
- parser_error

### 12.19 AI requests

Fields:

```text
id
household_id
provider
model
task_type
privacy_mode
approval_status
redacted_payload
response_payload
confidence_score
status
error_message nullable
created_at
completed_at nullable
```

Task types:

- classify_transaction
- enrich_vendor
- parse_receipt
- match_receipt
- suggest_category
- detect_subscription

Approval statuses:

- not_required
- pending
- approved
- rejected

### 12.20 Audit log

Fields:

```text
id
household_id
actor
action
entity_type
entity_id
details_json
created_at
```

Important actions:

- import_statement
- delete_transaction
- update_category
- create_rule
- approve_ai_request
- send_cloud_ai_request
- confirm_receipt_match

---

## 13. Standard Transaction Format

All importers must output this internal structure before database insert.

```json
{
  "source": {
    "institution": "Curve",
    "format": "csv",
    "filename": "curve-may-2026.csv"
  },
  "transaction": {
    "external_id": "optional",
    "transaction_date": "2026-05-31",
    "posted_date": "2026-06-01",
    "description_raw": "TESCO STORES 3142 DARTFORD",
    "merchant_raw": "TESCO STORES",
    "amount": -42.18,
    "currency": "GBP",
    "direction": "debit"
  }
}
```

Mandatory fields:

- transaction_date
- description_raw
- amount
- currency

Optional fields:

- posted_date
- merchant_raw
- external_id
- category_hint
- account_hint
- card_hint

---

## 14. Import Engine

### 14.1 Supported import priority

1. Curve CSV.
2. Barclays CSV.
3. Lloyds CSV.
4. Monzo CSV.
5. Generic CSV mapper.
6. PDF bank statements.
7. Receipt uploads.

### 14.2 Import workflow

```text
User uploads file
  ↓
Detect parser
  ↓
Parse rows
  ↓
Normalise transactions
  ↓
Validate transactions
  ↓
Detect duplicates
  ↓
Apply vendor aliases
  ↓
Apply rules
  ↓
Assign categories where possible
  ↓
Create review items for uncertain records
  ↓
Save statement and transactions
  ↓
Update dashboard sensors
```

### 14.3 Parser detection

Detection methods:

- Filename pattern.
- CSV headers.
- Institution selected by user.
- Content sniffing.
- User fallback.

Example:

```text
If headers include "Date", "Description", "Amount", "Currency", "Card":
  possible Curve
```

### 14.4 Generic CSV mapper

The generic importer should allow user to map columns:

- Date.
- Posted date.
- Description.
- Merchant.
- Debit.
- Credit.
- Amount.
- Currency.
- Category.
- Notes.

The mapping should be saved as a reusable import profile.

### 14.5 Duplicate detection

Use a source hash.

Potential duplicate key:

```text
account_id + transaction_date + amount + normalized_description
```

Better source hash:

```text
sha256(account_id|date|amount|currency|description_raw|posted_date)
```

Duplicate statuses:

- exact_duplicate
- possible_duplicate
- not_duplicate

Exact duplicates should be skipped or marked.

Possible duplicates should go to review.

### 14.6 Import error handling

Errors should not fail the whole import if some rows are valid.

Create an import report:

```text
Imported: 123
Skipped duplicates: 4
Rows with errors: 2
Needs review: 9
```

---

## 15. Categorisation Engine

### 15.1 Category assignment order

Use this order:

```text
1. User manual override
2. Explicit rule
3. Vendor default category
4. Historical user behaviour
5. Category library keyword match
6. Local AI suggestion
7. Cloud AI suggestion
8. Unknown category review item
```

### 15.2 Confidence score

Each classification should have a confidence.

Examples:

- Manual user assignment: 1.00.
- Exact vendor rule: 0.98.
- Vendor default: 0.90.
- Keyword match: 0.70.
- Local AI: model returned confidence.
- Cloud AI: model returned confidence.

### 15.3 Manual correction learning

When user changes category:

Prompt:

```text
Always categorise this vendor as this category?
```

Options:

- Yes, create vendor rule.
- Only this transaction.
- Create rule with conditions.
- Do not ask again for this vendor.

### 15.4 Category library

The category library is a core project asset.

It should live as versioned JSON.

Example:

```json
{
  "id": "food.groceries",
  "name": "Groceries",
  "parent_id": "food",
  "description": "Food and household grocery shopping.",
  "examples": ["Tesco", "Sainsbury's", "Lidl", "Aldi", "Asda"],
  "keywords": ["tesco", "sainsburys", "lidl", "aldi", "grocery"],
  "privacy_sensitivity": "normal",
  "is_budgetable": true,
  "icon": "mdi:cart",
  "colour": "#4CAF50"
}
```

### 15.5 Simple default categories

Initial category library:

```text
Income
Transfers
Housing
Bills
Groceries
Eating Out
Transport
Car
Shopping
Subscriptions
Health
Insurance
Travel
Pets
Entertainment
Home
DIY
Garden
Gifts
Cash
Fees
Unknown
```

### 15.6 Expandable advanced categories

Advanced categories should be optional.

Example:

```text
Home
  Mortgage/Rent
  Council Tax
  Insurance
  Utilities
  Furniture
  Appliances
  Smart Home
  Renovation
    Tools
    Materials
    Electrical
    Plumbing
    Painting
    Flooring
    Garden
```

### 15.7 Category expansion UX

User should be able to:

- Start with simple categories.
- Enable advanced category packs.
- Create custom categories.
- Merge categories.
- Archive categories.
- Move transactions between categories.
- Convert category into parent/child structure later.

---

## 16. Vendor Engine

### 16.1 Vendor normalisation

Goal:

```text
TESCO STORES 3142 DARTFORD -> Tesco
SCREWFIX DIRECT DARTFORD -> Screwfix
AMZNMKTPLACE*XYZ -> Amazon
```

### 16.2 Vendor alias matching

Vendor aliases should support:

- Exact match.
- Contains.
- Regex.
- Fuzzy match.
- User-created alias.
- AI-suggested alias.

### 16.3 Vendor enrichment

Vendors should have:

- Canonical name.
- Aliases.
- Default category.
- Service type.
- Notes.
- Website later.
- Last seen.
- Total spend.
- Number of transactions.
- Subscription flag.

### 16.4 Vendor review

Create review item if:

- Vendor cannot be resolved.
- Multiple possible vendors.
- Vendor alias conflicts.
- Vendor category changed.
- Vendor looks like a new subscription.

---

## 17. Split Transactions

### 17.1 Requirement

One transaction can be split into multiple categories/projects.

Examples:

```text
Amazon £120
  £40 Home / Tools
  £50 Pets
  £30 Household

Tesco £80
  £60 Groceries
  £12 Household
  £8 Health
```

### 17.2 Validation

Rules:

- Split total must equal transaction total.
- Currency must match.
- A split must have either category, project, or both.
- Negative/positive signs must be consistent.
- Editing split updates dashboard summaries.

### 17.3 UI

Transaction details page should allow:

- Add split.
- Remove split.
- Auto-balance remaining amount.
- Apply receipt items to split.
- Save split template.

### 17.4 Split templates

Future feature.

Example:

```text
Tesco default:
90% Groceries
10% Household
```

---

## 18. Projects and Tags

### 18.1 Projects

Projects are first-class because they support home renovation and major life events.

Examples:

- House purchase.
- Moving.
- Bathroom renovation.
- Kitchen renovation.
- Garden.
- Smart home.
- Tools.
- Holiday.
- Car maintenance.

### 18.2 Project features

Each project should show:

- Total spend.
- Budget.
- Spend by category.
- Spend by vendor.
- Transactions.
- Receipts.
- Timeline.
- Forecast later.

### 18.3 Tags

Tags are flexible labels.

Examples:

- reimbursable
- work
- warranty
- gift
- urgent
- needs_receipt
- shared
- personal

### 18.4 Project versus tag

Use project when there is a clear cost collection goal.

Use tag when it is a flexible label.

---

## 19. Budgets

### 19.1 MVP budget features

Budgets can be simple:

- Monthly budget per category.
- Monthly total budget.
- Project budget.
- Alert threshold.

### 19.2 Budget alerts

Examples:

- 80% spent.
- 100% spent.
- Overspent.
- Unusual spend compared to previous month.

### 19.3 Budget periods

Support:

- Monthly.
- Weekly.
- Yearly.
- Custom project period.

### 19.4 Rollover

Rollover can come later.

---

## 20. Recurring Payments and Subscriptions

### 20.1 Detection

Detect recurring payments by:

- Same vendor.
- Similar amount.
- Regular interval.
- Similar date each month.
- Known subscription category.

### 20.2 Subscription fields

```text
vendor_id
amount
currency
frequency
next_expected_date
last_seen_date
category_id
confidence_score
status
```

Statuses:

- active
- possible
- cancelled
- ignored

### 20.3 Alerts

Notify when:

- New subscription detected.
- Subscription amount changes.
- Subscription not seen when expected.
- Annual renewal approaching.

---

## 21. Receipt Upload and OCR

Receipt upload comes after CSV import and core finance features.

### 21.1 Receipt workflow

```text
User uploads receipt image/PDF
  ↓
Store original file
  ↓
Run OCR locally
  ↓
Extract merchant/date/total/VAT
  ↓
Match to transaction
  ↓
Create review item if uncertain
```

### 21.2 Receipt extraction levels

Level 1: simple receipt.

```text
merchant
date
total
VAT
currency
```

Level 2: item-level receipt.

```text
items
quantities
prices
item categories
```

Level 2 is optional.

### 21.3 OCR confidence

If OCR confidence is high:

- Save extracted values.
- Suggest match.

If OCR confidence is low:

- Create review item.
- Optionally ask local LLM.
- Optionally ask cloud AI depending on privacy mode.

### 21.4 Receipt matching

Matching score:

```text
amount match: 50 points
date proximity: 20 points
vendor similarity: 20 points
account/payment hint: 5 points
category agreement: 5 points
```

Thresholds:

- 90+ auto-match if allowed.
- 70-89 suggest.
- Under 70 keep unmatched.

### 21.5 Matching settings

User-selectable:

- Suggest only.
- Auto-match high confidence.
- Always ask.
- Allow one receipt to one transaction.
- Allow multiple receipts to one transaction.
- Allow one receipt to multiple transactions.
- Allow cash receipts.
- Allow partial refunds.
- Allow returns.

---

## 22. AI Gateway

### 22.1 Purpose

AI should be pluggable and optional.

It should help with:

- Vendor cleanup.
- Category suggestions.
- Receipt parsing.
- Receipt matching.
- Subscription detection.
- Vendor enrichment.
- Review explanations.

AI must not be the source of truth.

### 22.2 Provider abstraction

Interface:

```python
class AIProvider:
    async def classify_transaction(self, transaction, categories):
        raise NotImplementedError

    async def enrich_vendor(self, vendor_name):
        raise NotImplementedError

    async def parse_receipt(self, ocr_text):
        raise NotImplementedError

    async def match_receipt(self, receipt, candidate_transactions):
        raise NotImplementedError
```

Providers:

- NoAIProvider.
- LocalOpenAICompatibleProvider.
- OllamaProvider.
- CloudOpenAICompatibleProvider.
- AnthropicProvider later.
- GeminiProvider later.
- XAIProvider later.

### 22.3 AI routing order

```text
Rules first
Vendor library second
Local LLM third
Cloud AI fourth
Manual review last
```

### 22.4 AI payload redaction

Before cloud AI:

Remove:

- account numbers
- sort codes
- card numbers
- full statement context
- addresses
- salary references if sensitive
- names where unnecessary

Send only minimal payload.

Example:

```json
{
  "description": "SCREWFIX DIRECT DARTFORD",
  "amount": -38.99,
  "currency": "GBP",
  "candidate_categories": [
    "DIY",
    "Home",
    "Shopping"
  ]
}
```

### 22.5 Cloud AI approval

For manual approval mode:

```text
This item needs cloud AI.
Payload preview:
...
Approve / reject
```

### 22.6 AI audit

Every AI call must be logged.

Log:

- provider
- model
- task
- redacted payload
- response
- approval mode
- timestamp
- status

---

## 23. Review Queue

### 23.1 Purpose

The review queue is the main safety mechanism.

Review items are created for:

- Unknown vendors.
- Unknown categories.
- Duplicate transactions.
- Low-confidence matches.
- Receipt match suggestions.
- Cloud AI approval requests.
- Sensitive transactions.
- Import errors.
- Split validation issues.
- New subscription detection.

### 23.2 Review UI

Each item should show:

- Issue.
- Related transaction/receipt/vendor.
- Suggested action.
- Confidence.
- Approve/reject/fix controls.

### 23.3 Review actions

Examples:

- Set category.
- Create rule.
- Confirm vendor alias.
- Confirm receipt match.
- Reject duplicate.
- Approve AI request.
- Ignore.
- Mark as resolved.

---

## 24. API Design

### 24.1 Health

```http
GET /api/health
```

Response:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

### 24.2 Settings

```http
GET /api/settings
PUT /api/settings
```

### 24.3 Imports

```http
POST /api/imports/upload
GET /api/imports
GET /api/imports/{id}
POST /api/imports/{id}/confirm
DELETE /api/imports/{id}
```

Upload response:

```json
{
  "import_id": "imp_123",
  "detected_parser": "curve_csv",
  "rows_detected": 120,
  "preview": [],
  "warnings": []
}
```

### 24.4 Transactions

```http
GET /api/transactions
GET /api/transactions/{id}
PATCH /api/transactions/{id}
DELETE /api/transactions/{id}
POST /api/transactions/{id}/split
POST /api/transactions/{id}/categorise
POST /api/transactions/{id}/mark-transfer
```

Filters:

- date_from
- date_to
- account_id
- category_id
- vendor_id
- project_id
- needs_review
- amount_min
- amount_max
- search

### 24.5 Categories

```http
GET /api/categories
POST /api/categories
PATCH /api/categories/{id}
DELETE /api/categories/{id}
POST /api/categories/import-library
POST /api/categories/enable-pack
```

### 24.6 Vendors

```http
GET /api/vendors
GET /api/vendors/{id}
POST /api/vendors
PATCH /api/vendors/{id}
POST /api/vendors/{id}/aliases
POST /api/vendors/{id}/set-default-category
```

### 24.7 Rules

```http
GET /api/rules
POST /api/rules
PATCH /api/rules/{id}
DELETE /api/rules/{id}
POST /api/rules/test
```

### 24.8 Projects

```http
GET /api/projects
POST /api/projects
PATCH /api/projects/{id}
DELETE /api/projects/{id}
GET /api/projects/{id}/summary
```

### 24.9 Budgets

```http
GET /api/budgets
POST /api/budgets
PATCH /api/budgets/{id}
DELETE /api/budgets/{id}
GET /api/budgets/summary
```

### 24.10 Receipts

```http
POST /api/receipts/upload
GET /api/receipts
GET /api/receipts/{id}
POST /api/receipts/{id}/ocr
POST /api/receipts/{id}/match
POST /api/receipts/{id}/confirm-match
```

### 24.11 Review

```http
GET /api/review
GET /api/review/{id}
POST /api/review/{id}/resolve
POST /api/review/{id}/ignore
```

### 24.12 Dashboard

```http
GET /api/dashboard/summary
GET /api/dashboard/monthly
GET /api/dashboard/categories
GET /api/dashboard/vendors
GET /api/dashboard/projects
GET /api/dashboard/subscriptions
```

---

## 25. Frontend Pages

### 25.1 Dashboard

Cards:

- This month spend.
- Income.
- Net.
- Top categories.
- Top vendors.
- Review queue count.
- Budget status.
- Project totals.
- Subscription total.
- Unmatched receipts.

Charts:

- Monthly spend.
- Category breakdown.
- Vendor breakdown.
- Project spend.
- Account spend.

### 25.2 Import

Features:

- Upload CSV.
- Select bank/parser.
- Preview detected rows.
- Map columns for generic CSV.
- Confirm import.
- Show import report.

### 25.3 Transactions

Features:

- Table.
- Filters.
- Search.
- Bulk categorise.
- Bulk assign project.
- Edit transaction.
- Split transaction.
- Mark transfer.
- Mark income.
- Link receipt.

### 25.4 Transaction detail

Show:

- Raw description.
- Normalised vendor.
- Amount.
- Account.
- Category.
- Project.
- Splits.
- Receipts.
- Matching history.
- Rules applied.
- AI suggestions.
- Audit history.

### 25.5 Categories

Features:

- View category tree.
- Enable advanced packs.
- Add/edit/delete categories.
- Merge categories.
- Archive categories.
- View category spend.

### 25.6 Vendors

Features:

- Vendor list.
- Aliases.
- Default category.
- Spend over time.
- Transactions.
- Subscription flag.
- Merge vendors.

### 25.7 Rules

Features:

- Rule list.
- Rule builder.
- Test rule against transactions.
- Enable/disable.
- Reorder priority.

### 25.8 Projects

Features:

- Project list.
- Project dashboard.
- Budget.
- Transactions.
- Receipts.
- Vendor/category breakdown.

### 25.9 Budgets

Features:

- Category budgets.
- Project budgets.
- Monthly overview.
- Alert thresholds.

### 25.10 Receipts

Later.

Features:

- Upload.
- OCR status.
- Match suggestions.
- Receipt detail.
- Item-level extraction optional.

### 25.11 Review queue

Features:

- Review cards.
- Suggested actions.
- Approve/reject.
- Create rule from review.
- Bulk resolve.

### 25.12 Settings

Sections:

- Setup mode.
- Privacy mode.
- Currency.
- Accounts.
- Import profiles.
- AI providers.
- OCR.
- MQTT.
- Home Assistant sensors.
- Backup/export.
- Advanced.

---

## 26. Home Assistant Add-on Design

### 26.1 Add-on config

Example `addon/config.yaml`:

```yaml
name: HA Finance Intelligence
version: "0.1.0"
slug: ha_finance_intelligence
description: Local-first personal finance app for Home Assistant
arch:
  - aarch64
  - amd64
startup: services
boot: auto
ingress: true
ingress_port: 8099
panel_icon: mdi:cash-multiple
panel_title: Finance
init: false
map:
  - addon_config:rw
  - homeassistant_config:rw
options:
  database_path: /config/finance/finance.db
  currency: GBP
  privacy_mode: strict_local
  mqtt_enabled: true
schema:
  database_path: str
  currency: str
  privacy_mode: list(strict_local|local_llm|cloud_manual|cloud_auto|no_ai)
  mqtt_enabled: bool
```

### 26.2 Add-on Dockerfile

High-level:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY backend/pyproject.toml backend/
COPY backend/app backend/app
COPY frontend/dist frontend/dist

RUN pip install ./backend

EXPOSE 8099

CMD ["python", "-m", "app.main"]
```

### 26.3 Ingress handling

Backend serves:

- API under `/api`.
- Frontend under `/`.
- Static assets.

The app should respect forwarded headers from Home Assistant ingress.

### 26.4 Storage

Use add-on config path.

Recommended paths:

```text
/config/finance/finance.db
/config/finance/uploads/
/config/finance/receipts/
/config/finance/exports/
/config/finance/backups/
```

### 26.5 Backup

Home Assistant backups should include add-on config data.

Also support manual export:

- database export
- CSV export
- category library export
- rules export
- vendor library export

---

## 27. MQTT Sensor Publishing

### 27.1 Publish cadence

Publish sensors after:

- import completed
- transaction updated
- category updated
- budget updated
- receipt matched
- scheduled daily refresh
- app startup

### 27.2 Discovery

Publish MQTT discovery configs for enabled sensors.

### 27.3 Sensor examples

Spend this month:

```json
{
  "name": "Finance Spend This Month",
  "unique_id": "finance_spend_this_month",
  "state_topic": "homeassistant/finance/state/spend_this_month",
  "unit_of_measurement": "GBP",
  "device_class": "monetary",
  "state_class": "measurement",
  "icon": "mdi:cash-minus"
}
```

Review items:

```json
{
  "name": "Finance Review Items",
  "unique_id": "finance_review_items",
  "state_topic": "homeassistant/finance/state/review_items",
  "icon": "mdi:alert-circle"
}
```

Project total:

```json
{
  "name": "Finance House Project Total",
  "unique_id": "finance_project_house_total",
  "state_topic": "homeassistant/finance/state/project_house_total",
  "unit_of_measurement": "GBP",
  "device_class": "monetary",
  "state_class": "measurement",
  "icon": "mdi:home-currency-usd"
}
```

---

## 28. Security Requirements

### 28.1 Data sensitivity

The app stores:

- bank transactions
- spending habits
- salaries
- mortgage payments
- medical/legal/insurance payments
- household behaviour
- receipts
- potentially addresses on receipts

Treat all data as sensitive.

### 28.2 Default security

- No external calls by default.
- No telemetry by default.
- No public exposure.
- No cloud AI by default.
- No raw statement sent to AI.
- No receipt image sent to AI unless user explicitly allows.
- Log every external request.

### 28.3 Redaction

Before cloud AI:

- Remove account/card numbers.
- Remove sort codes.
- Remove addresses.
- Remove names where not needed.
- Remove full statement context.
- Send one transaction/receipt at a time.

### 28.4 Sensitive category blocking

Categories can be marked:

- normal
- sensitive
- never_cloud

`never_cloud` transactions are never sent externally.

Suggested never-cloud categories:

- Income / Salary.
- Mortgage.
- Medical.
- Legal.
- Loans.
- Tax.
- Insurance.
- Transfers to family/partner.

### 28.5 Audit log

Audit:

- imports
- deletes
- manual edits
- rule creation
- AI requests
- cloud approvals
- receipt matches
- category merges

---

## 29. Implementation Roadmap

### Stage 0 — Project skeleton

Deliverables:

- Repository structure.
- FastAPI app.
- React app.
- SQLite database.
- SQLAlchemy models.
- Alembic migrations.
- Health endpoint.
- Add-on Dockerfile.
- Basic Home Assistant ingress UI.

Acceptance criteria:

- Add-on starts.
- UI opens from Home Assistant sidebar.
- `/api/health` returns OK.
- Database initialises.

### Stage 1 — CSV import MVP

Deliverables:

- Curve CSV parser.
- Barclays CSV parser.
- Lloyds CSV parser.
- Monzo CSV parser.
- Generic CSV parser.
- Import preview.
- Import confirmation.
- Duplicate detection.
- Transactions table.

Acceptance criteria:

- User uploads Curve CSV.
- Transactions appear in UI.
- Duplicate upload does not duplicate transactions.
- Import report is shown.

### Stage 2 — Categories and vendors

Deliverables:

- Simple category library.
- Category CRUD.
- Vendor normalisation.
- Vendor aliases.
- Manual categorisation.
- Category assignment on transaction.

Acceptance criteria:

- User can categorise transactions.
- Vendor aliases work.
- Dashboard groups by category.

### Stage 3 — Rules and learning

Deliverables:

- Rule engine.
- Rule UI.
- Create rule from manual correction.
- Re-run rules on uncategorised transactions.

Acceptance criteria:

- User changes Screwfix to DIY.
- User creates rule.
- Future Screwfix transactions are categorised automatically.

### Stage 4 — Split transactions

Deliverables:

- Transaction splits model.
- Split UI.
- Validation.
- Dashboard calculations based on splits.

Acceptance criteria:

- User splits Amazon transaction across categories.
- Category totals use split amounts.

### Stage 5 — Projects and tags

Deliverables:

- Projects CRUD.
- Tags CRUD.
- Assign transaction/split to project.
- Project dashboard.

Acceptance criteria:

- User creates Bathroom Renovation project.
- Transactions can be assigned.
- Project total appears.

### Stage 6 — Budgets and alerts

Deliverables:

- Category budgets.
- Project budgets.
- Alert thresholds.
- HA sensor values.
- MQTT discovery.

Acceptance criteria:

- Budget progress visible.
- HA sensor shows spend.
- HA notification automation can trigger.

### Stage 7 — Review queue

Deliverables:

- Review item model.
- Review page.
- Unknown vendor review.
- Duplicate review.
- Low-confidence category review.

Acceptance criteria:

- Unknown transactions appear in review queue.
- User resolves review item.
- Sensor count updates.

### Stage 8 — Receipts

Deliverables:

- Receipt upload.
- Receipt storage.
- Local OCR.
- Extract date/vendor/total.
- Receipt matching.

Acceptance criteria:

- User uploads receipt.
- OCR extracts total.
- App suggests matching transaction.

### Stage 9 — Local AI

Deliverables:

- AI provider abstraction.
- NoAI provider.
- Local OpenAI-compatible provider.
- Ollama provider.
- AI classification task.
- AI audit log.

Acceptance criteria:

- User enables local LLM.
- Unknown transaction can be classified.
- Result is JSON and validated.
- No cloud call occurs.

### Stage 10 — Cloud AI approval

Deliverables:

- Cloud OpenAI-compatible provider.
- Redaction.
- Manual approval workflow.
- Cloud audit log.
- Sensitive category blocking.

Acceptance criteria:

- User sees payload before sending.
- User approves.
- AI response is stored.
- Audit log shows request.

### Stage 11 — PDF imports

Deliverables:

- PDF upload.
- Text extraction.
- Table extraction.
- Statement parser interface.
- Review-heavy import flow.

Acceptance criteria:

- User uploads PDF statement.
- Transactions are extracted or flagged for review.

### Stage 12 — Open-source polish

Deliverables:

- README.
- Install docs.
- Development docs.
- Contribution guide.
- Category library docs.
- Parser contribution guide.
- Sample data.
- Tests.
- GitHub Actions.

Acceptance criteria:

- Another user can install and import sample CSV.
- New parser can be contributed using documented interface.

---

## 30. Build Tasks for AI Coding Assistant

Use this section directly in VSCode/Copilot/Cline/Cursor.

### 30.1 Initial prompt

```text
Build a Home Assistant-first local personal finance app called HA Finance Intelligence.

Use Python FastAPI backend, SQLite with SQLAlchemy/Alembic, React TypeScript frontend, and a Home Assistant add-on wrapper with ingress support.

Start with:
- health endpoint
- SQLite database setup
- models for household, account, statement, transaction, category, vendor, vendor_alias, rule, project, tag, budget, review_item
- React dashboard shell
- transaction import page
- basic Home Assistant add-on config
```

### 30.2 Backend task 1

```text
Create a FastAPI backend with:
- /api/health endpoint
- config loading from environment variables
- SQLite database connection
- SQLAlchemy models
- Alembic migrations
- structured logging
- CORS configurable for local development
```

### 30.3 Backend task 2

```text
Implement CSV import service with parser interface:
- BaseStatementParser
- CurveCsvParser
- BarclaysCsvParser
- LloydsCsvParser
- MonzoCsvParser
- GenericCsvParser placeholder

Each parser should return StandardTransaction objects.
Add duplicate detection using source hash.
Add import preview and import confirmation endpoints.
```

### 30.4 Backend task 3

```text
Implement category and vendor services:
- default category library loaded from JSON
- category CRUD
- vendor CRUD
- vendor alias matching
- assign category to transaction
- assign vendor to transaction
```

### 30.5 Backend task 4

```text
Implement rule engine:
- rule model
- conditions: merchant_contains, description_contains, vendor_equals, account_equals, amount_between
- actions: set_category, set_vendor, set_project, require_review
- apply rules during import
- re-run rules endpoint
```

### 30.6 Backend task 5

```text
Implement split transactions:
- transaction_splits table
- API to create/update/delete splits
- validation that split totals equal transaction amount
- dashboard summaries should use split rows when transaction is split
```

### 30.7 Frontend task 1

```text
Create React TypeScript frontend with:
- sidebar navigation
- Dashboard page
- Import page
- Transactions page
- Categories page
- Vendors page
- Rules page
- Projects page
- Review Queue page
- Settings page
Use TanStack Query for API calls and TanStack Table for transaction table.
```

### 30.8 Frontend task 2

```text
Build Import UI:
- file upload
- parser selection
- import preview table
- import report
- confirm import button
- display errors and warnings
```

### 30.9 Frontend task 3

```text
Build Transactions UI:
- transaction table
- filters by date/account/category/vendor/project/review status
- edit category/vendor/project
- open transaction detail drawer
- split transaction editor
```

### 30.10 Home Assistant task

```text
Create Home Assistant add-on wrapper:
- addon/config.yaml
- Dockerfile
- run script
- expose app on internal port 8099
- enable ingress
- store data under /config/finance
```

### 30.11 MQTT task

```text
Implement MQTT publisher:
- publish discovery configs for finance sensors
- publish spend_this_month, income_this_month, net_this_month, review_items, unknown_transactions, subscriptions_total
- allow MQTT to be disabled in settings
```

---

## 31. Acceptance Criteria for MVP

MVP is complete when:

1. Add-on installs and starts in Home Assistant.
2. Finance UI appears in Home Assistant sidebar.
3. User can upload a Curve CSV.
4. User can upload Barclays/Lloyds/Monzo CSV or generic CSV with mapping.
5. Transactions are stored without duplicates.
6. User can categorise transactions.
7. User can create vendor aliases.
8. User can create rules from corrections.
9. User can split transactions.
10. User can create projects and assign spend to projects.
11. Dashboard shows monthly totals.
12. Review queue shows unknown transactions.
13. MQTT sensors publish to Home Assistant.
14. Strict local mode is default.
15. No external network calls occur by default.

---

## 32. Testing Strategy

### 32.1 Unit tests

Test:

- CSV parsers.
- Date parsing.
- Amount parsing.
- Duplicate detection.
- Rule engine.
- Category assignment.
- Vendor alias matching.
- Split validation.
- Dashboard calculations.

### 32.2 Integration tests

Test:

- Upload file.
- Preview import.
- Confirm import.
- Categorise transaction.
- Create rule.
- Re-import similar transaction.
- MQTT publish.

### 32.3 Fixture data

Use anonymised sample CSV files.

Do not commit real bank data.

Create fake examples:

- Curve sample.
- Barclays sample.
- Lloyds sample.
- Monzo sample.
- Generic sample.

### 32.4 Privacy tests

Test:

- Strict local mode blocks AI.
- Cloud AI disabled by default.
- Sensitive categories blocked from cloud.
- Redaction removes account/card numbers.

---

## 33. Category Library v0.1

Initial categories:

```json
[
  {
    "id": "income",
    "name": "Income",
    "parent_id": null,
    "icon": "mdi:cash-plus",
    "privacy_sensitivity": "sensitive",
    "is_budgetable": false
  },
  {
    "id": "transfers",
    "name": "Transfers",
    "parent_id": null,
    "icon": "mdi:bank-transfer",
    "privacy_sensitivity": "sensitive",
    "is_budgetable": false
  },
  {
    "id": "housing",
    "name": "Housing",
    "parent_id": null,
    "icon": "mdi:home",
    "privacy_sensitivity": "sensitive",
    "is_budgetable": true
  },
  {
    "id": "bills",
    "name": "Bills",
    "parent_id": null,
    "icon": "mdi:receipt",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "groceries",
    "name": "Groceries",
    "parent_id": null,
    "icon": "mdi:cart",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "eating_out",
    "name": "Eating Out",
    "parent_id": null,
    "icon": "mdi:silverware-fork-knife",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "transport",
    "name": "Transport",
    "parent_id": null,
    "icon": "mdi:train-car",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "car",
    "name": "Car",
    "parent_id": null,
    "icon": "mdi:car",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "shopping",
    "name": "Shopping",
    "parent_id": null,
    "icon": "mdi:shopping",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "subscriptions",
    "name": "Subscriptions",
    "parent_id": null,
    "icon": "mdi:repeat",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "health",
    "name": "Health",
    "parent_id": null,
    "icon": "mdi:medical-bag",
    "privacy_sensitivity": "sensitive",
    "is_budgetable": true
  },
  {
    "id": "insurance",
    "name": "Insurance",
    "parent_id": null,
    "icon": "mdi:shield-check",
    "privacy_sensitivity": "sensitive",
    "is_budgetable": true
  },
  {
    "id": "travel",
    "name": "Travel",
    "parent_id": null,
    "icon": "mdi:airplane",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "pets",
    "name": "Pets",
    "parent_id": null,
    "icon": "mdi:paw",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "entertainment",
    "name": "Entertainment",
    "parent_id": null,
    "icon": "mdi:movie",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "home",
    "name": "Home",
    "parent_id": null,
    "icon": "mdi:home-modern",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "diy",
    "name": "DIY",
    "parent_id": null,
    "icon": "mdi:hammer-screwdriver",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "garden",
    "name": "Garden",
    "parent_id": null,
    "icon": "mdi:flower",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "gifts",
    "name": "Gifts",
    "parent_id": null,
    "icon": "mdi:gift",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "cash",
    "name": "Cash",
    "parent_id": null,
    "icon": "mdi:cash",
    "privacy_sensitivity": "sensitive",
    "is_budgetable": true
  },
  {
    "id": "fees",
    "name": "Fees",
    "parent_id": null,
    "icon": "mdi:bank-minus",
    "privacy_sensitivity": "normal",
    "is_budgetable": true
  },
  {
    "id": "unknown",
    "name": "Unknown",
    "parent_id": null,
    "icon": "mdi:help-circle",
    "privacy_sensitivity": "normal",
    "is_budgetable": false
  }
]
```

---

## 34. Advanced Category Pack v0.1

```text
Home
  Mortgage/Rent
  Council Tax
  Utilities
    Electricity
    Gas
    Water
    Internet
    Mobile
  Insurance
  Furniture
  Appliances
  Smart Home
  Renovation
    Tools
    Materials
    Electrical
    Plumbing
    Painting
    Flooring
    Garden
    Waste/Skip

Food
  Groceries
  Restaurants
  Takeaway
  Coffee
  Work Lunch

Transport
  Public Transport
  Fuel
  Parking
  Taxi/Rideshare
  Car Maintenance
  Car Insurance
  Road Tax

Shopping
  Clothing
  Electronics
  Household
  Online Shopping
  Amazon

Subscriptions
  Streaming
  Music
  Cloud/Software
  Gym
  News
  Gaming
```

---

## 35. Parser Interface

```python
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class StandardTransaction:
    transaction_date: date
    amount: Decimal
    currency: str
    description_raw: str
    posted_date: Optional[date] = None
    merchant_raw: Optional[str] = None
    external_id: Optional[str] = None
    account_hint: Optional[str] = None
    category_hint: Optional[str] = None


class BaseStatementParser:
    parser_id: str
    institution: str
    format: str = "csv"

    def can_parse(self, filename: str, content: bytes) -> bool:
        raise NotImplementedError

    def parse(self, filename: str, content: bytes) -> list[StandardTransaction]:
        raise NotImplementedError
```

---

## 36. Rule Engine Interface

```python
class RuleEngine:
    def apply_rules(self, transaction):
        matching_rules = self.find_matching_rules(transaction)

        for rule in matching_rules:
            self.apply_action(transaction, rule)

        return transaction
```

Example rule:

```json
{
  "name": "Screwfix to DIY",
  "priority": 100,
  "enabled": true,
  "condition_type": "description_contains",
  "condition_value": "SCREWFIX",
  "action_type": "set_category",
  "action_value": "diy"
}
```

---

## 37. Dashboard Calculations

### 37.1 Spend this month

Sum all debit transactions in current calendar month.

If transaction is split, use splits.

Exclude:

- transfers
- duplicate transactions
- ignored transactions
- income

### 37.2 Income this month

Sum all credit transactions marked income.

### 37.3 Net this month

```text
income - spending
```

### 37.4 Category spend

For each category:

- Use split amount if split exists.
- Otherwise use transaction category.

### 37.5 Project spend

For each project:

- Use transaction project if set.
- Use split project if split exists.

### 37.6 Review count

Count unresolved review items.

---

## 38. Configuration

### 38.1 Main settings

```json
{
  "currency": "GBP",
  "privacy_mode": "strict_local",
  "setup_mode": "household",
  "default_import_account": null,
  "mqtt_enabled": true,
  "ai_enabled": false,
  "ocr_enabled": false,
  "receipt_item_tracking": false,
  "auto_match_receipts": false,
  "auto_create_rules": false
}
```

### 38.2 Privacy settings

```json
{
  "cloud_ai_allowed": false,
  "cloud_ai_requires_approval": true,
  "send_receipt_images_to_cloud": false,
  "send_full_statements_to_cloud": false,
  "redaction_enabled": true,
  "sensitive_categories_block_cloud": true
}
```

---

## 39. Open-Source Readiness

When ready:

### 39.1 License

Recommended:

- AGPLv3 if you want to protect against commercial hosted forks.
- Apache 2.0 if you want maximum adoption.

### 39.2 Documentation needed

- Install in Home Assistant.
- Development setup.
- Privacy model.
- Category library.
- Bank parser guide.
- AI provider guide.
- Receipt OCR guide.
- Backup/restore.
- Troubleshooting.

### 39.3 Contribution areas

- Bank parsers.
- Category library.
- Vendor alias packs.
- Receipt OCR templates.
- Translations.
- Dashboard cards.
- AI provider integrations.

---

## 40. Immediate Next Build Step

Start with this exact MVP slice:

```text
1. FastAPI backend.
2. SQLite database.
3. React frontend.
4. Home Assistant add-on shell with ingress.
5. Curve CSV parser.
6. Transactions table.
7. Default categories.
8. Manual categorisation.
9. MQTT sensor for spend this month.
```

Do not start with OCR, AI or PDF parsing.

Once this works, everything else has somewhere to attach.

---

## 41. First Milestone Definition

### Milestone 1 name

**M1: Local CSV Finance Core**

### Deliverables

- Home Assistant add-on starts.
- UI opens from HA sidebar.
- Upload Curve CSV.
- Transactions imported.
- Duplicate detection.
- Default categories loaded.
- Manual categorisation.
- Dashboard monthly spend.
- MQTT sensor published.
- Strict local mode default.

### Out of scope

- AI.
- OCR.
- PDF.
- Budgets.
- Receipts.
- Open Banking.

### Success criteria

You can import one month of Curve transactions, categorise them, see spending by category, and expose monthly spend to Home Assistant.

---

## 42. Suggested First GitHub Issues

1. Create repository skeleton.
2. Create FastAPI backend.
3. Create SQLite models and migrations.
4. Create React frontend shell.
5. Create Home Assistant add-on config.
6. Add ingress support.
7. Implement category library v0.1.
8. Implement Curve CSV parser.
9. Implement import preview endpoint.
10. Implement import confirmation endpoint.
11. Implement duplicate detection.
12. Implement transactions table UI.
13. Implement category assignment UI.
14. Implement dashboard summary endpoint.
15. Implement MQTT discovery and state publishing.
16. Add tests for Curve CSV parser.
17. Add sample anonymised Curve CSV.
18. Write README install instructions.

---

## 43. Design Principle Summary

The project should follow these principles:

```text
Home Assistant first.
Strict local by default.
CSV before PDF.
Rules before AI.
User correction beats AI.
Bank transaction is source of truth.
Receipt enriches transaction.
AI is assistant, not authority.
Category library is a core asset.
Everything uncertain goes to review.
Everything external is auditable.
Start simple but design for household/project expansion.
```

---

## 44. Source Notes

This document assumes a Home Assistant add-on/app style deployment. Home Assistant apps/add-ons allow users to extend functionality around Home Assistant and are configured through the Supervisor panel. Home Assistant ingress can present an add-on web UI in the Home Assistant sidebar and Home Assistant handles authentication for that UI. MQTT sensors are a practical first integration path for exposing calculated finance values into Home Assistant.

References:

- Home Assistant app/add-on development: https://developers.home-assistant.io/docs/apps/
- Home Assistant app configuration: https://developers.home-assistant.io/docs/apps/configuration/
- Home Assistant app presentation and ingress: https://developers.home-assistant.io/docs/apps/presentation
- Home Assistant MQTT integration: https://www.home-assistant.io/integrations/mqtt/
- Home Assistant MQTT sensor integration: https://www.home-assistant.io/integrations/sensor.mqtt/
