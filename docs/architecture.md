# Architecture diagrams

Visual companion to [`ha_finance_intelligence_spec.md`](../ha_finance_intelligence_spec.md)
and [`docs/context.md`](context.md). Backlog #94.

> **Prefer a browser?** [**architecture.html**](architecture.html) is a
> self-contained, styled version of these diagrams (inline SVG — renders offline,
> light/dark, no external scripts). This Markdown file keeps the same diagrams as
> [Mermaid](https://mermaid.js.org) so they render on GitHub and in VS Code.

Dashed = optional / opt-in / later stage.

## 1. System & infrastructure

```mermaid
flowchart TB
  user["Browser / HA mobile app"]

  subgraph ha["Home Assistant"]
    ingress["Ingress sidebar panel<br/>(handles auth)"]
    mqtt["MQTT broker"]
    autos["Automations / notifications"]
  end

  subgraph addon["HA Finance Intelligence add-on (one container)"]
    fe["React + TS frontend"]
    api["FastAPI backend /api"]
    svc["Services<br/>import · categorise · rules · fx · dashboard · budgets ·<br/>projects · savings · investments · assets · receipts/OCR ·<br/>subscriptions · analytics · search · energy · AI gateway · retention · backup"]
    db[("SQLite<br/>/data/finance.db (private)")]
    fe --> api --> svc --> db
  end

  user --> ingress --> fe
  svc -. "publish sensors (opt-in)" .-> mqtt
  mqtt -. triggers .-> autos
  svc -. "FX rates (opt-in)" .-> frank["api.frankfurter.dev"]
  svc -. "AI (opt-in)" .-> ai["local / cloud LLM"]
  svc -. "prices (opt-in)" .-> px["Stooq / Alpha Vantage"]
  svc -. "documents (opt-in)" .-> ppl["your Paperless-ngx"]

  classDef opt stroke-dasharray:5 5,fill:#f7f7f7,color:#555;
  class mqtt,autos,frank,ai,px,ppl opt;
```

> Standalone (no Home Assistant): the same container runs behind an optional
> TLS reverse proxy instead of HA ingress — see [reverse-proxy.md](reverse-proxy.md).

## 2. Request flow

```mermaid
sequenceDiagram
  actor U as Browser
  participant HA as HA Ingress
  participant API as FastAPI (/api)
  participant S as Service layer
  participant DB as SQLite
  U->>HA: request under /api/hassio_ingress/<token>/
  HA->>API: proxied (forwarded headers)
  API->>S: call service
  S->>DB: query / write
  DB-->>S: rows
  S-->>API: result
  API-->>U: JSON (API) or index.html (SPA)
```

## 3. Import & categorisation flow

```mermaid
sequenceDiagram
  actor U as User
  participant API
  participant IS as import_service
  participant P as parser
  participant R as rule_service
  participant V as vendor_service
  participant C as category_service
  participant FX as fx_service

  U->>API: POST /api/imports/upload (CSV)
  API->>IS: create_import
  IS->>P: detect parser + parse rows
  IS->>IS: source-hash dedup
  IS-->>U: preview + report (pending statement)

  U->>API: POST /api/imports/{id}/confirm
  API->>IS: confirm_import
  loop each new transaction
    IS->>R: apply rules (manual > rule)
    IS->>V: vendor alias + default category
    IS->>C: keyword fallback
    IS->>FX: convert to base currency
  end
  IS-->>U: imported (N new / M duplicates)
```

## 4. Data model (core entities)

The full model has ~30 tables (spec §12); this shows the core relationships. Other
groups hang off the same `ACCOUNT`/`TRANSACTION`/`USER` spine: **savings**
(`SavingsBalance`, `SavingsGoal`), **investments** (`AccountValue`, `Holding`,
`HoldingPrice`), **assets** (`Asset`, `AssetLog` for cars/home), **receipts**
(`Receipt`, `ReceiptMatch`), **AI** (`AIRequest`), **review** (`ReviewItem`),
**subscriptions**, **rules**, **child allowance** (`ChildAllocation`), **MFA**
(`UserSession`) and **audit** (`AuditLog`). Retention adds an `archived_at` column
to transactions / receipts / AI-requests / audit-logs.

```mermaid
erDiagram
  HOUSEHOLD ||--o{ USER : has
  HOUSEHOLD ||--o{ ACCOUNT : has
  ACCOUNT ||--o{ STATEMENT : produces
  STATEMENT ||--o{ TRANSACTION : contains
  TRANSACTION ||--o{ TRANSACTION_SPLIT : "splits into"
  TRANSACTION }o--o| CATEGORY : "categorised as"
  TRANSACTION }o--o| VENDOR : "merchant"
  TRANSACTION }o--o| PROJECT : "tagged to"
  TRANSACTION ||--o{ RECEIPT_MATCH : "matched by"
  RECEIPT ||--o{ RECEIPT_MATCH : "matches"
  ACCOUNT ||--o{ SAVINGS_BALANCE : "savings snapshots"
  ACCOUNT ||--o{ HOLDING : "investment positions"
  CATEGORY ||--o{ CATEGORY : "parent of"
  VENDOR ||--o{ VENDOR_ALIAS : "known as"
  VENDOR }o--o| CATEGORY : "default category"
  RULE }o--o| CATEGORY : "set_category"
  BUDGET }o--o| CATEGORY : "limits"
  FX_RATE }o--o| TRANSACTION : "converts (by date/currency)"
```

## 5. Deployment (add-on image)

```mermaid
flowchart LR
  subgraph build["docker build"]
    n["node:22<br/>npm run build"] --> dist["frontend/dist"]
  end
  subgraph img["python:3.12-slim runtime"]
    be["backend (pip install)"]
    dist --> served["served at /"]
    be --> served
    run["run.sh<br/>options -> env, migrate, start"]
  end
  vol[("/data volume<br/>db + uploads (private, backed up)")]
  img --- vol
```

## Keeping these current

Update a diagram when the corresponding shape changes (a new external
dependency, a new core entity, a changed flow). They are intentionally
high-level — the source of truth is the code and the spec.
