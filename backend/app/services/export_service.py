"""CSV export of transactions and dashboard summaries (spec §24.4, §25.1; #132).

A CSV can't embed charts, so we export the *data* behind the dashboard's charts
(category totals, the monthly trend series) plus the raw transactions — the user
keeps the in-app charts for the visuals. Names (category/project/account/vendor)
are resolved through small id→name maps built once per export, so there's no
N+1 even on a large statement history.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Account, Category, Project, Tag, Transaction, Vendor
from app.services import dashboard_service, settings_service
from app.services.analytics_service import monthly_series
from app.services.scope import account_scope_condition, archived_condition

# Personal-finance histories are small, but cap the row count so a pathological
# export can't exhaust memory.
MAX_EXPORT_ROWS = 100_000

TRANSACTION_HEADERS = [
    "date",
    "posted_date",
    "description",
    "merchant",
    "amount",
    "currency",
    "base_amount",
    "base_currency",
    "direction",
    "category",
    "project",
    "account",
    "tags",
    "is_split",
    "is_transfer",
    "is_income",
    "is_business",
    "vat_amount",
    "needs_review",
    "review_reason",
]


def _equality_conditions(
    *,
    transaction_id: int | None,
    date_from: date | None,
    date_to: date | None,
    account_id: int | None,
    category_id: int | None,
    vendor_id: int | None,
    project_id: int | None,
    tag_id: int | None,
    needs_review: bool | None,
    is_business: bool | None,
    amount_min: Decimal | None,
    amount_max: Decimal | None,
) -> list:
    """The straightforward equality/comparison filters (each appended only when set)."""
    conditions: list = []
    if transaction_id is not None:
        # The focus deep-link (Review-Queue "Open transaction →", trip drill-down)
        # narrows the list to a single row so it's always surfaced regardless of
        # which page it would otherwise fall on.
        conditions.append(Transaction.id == transaction_id)
    if date_from is not None:
        conditions.append(Transaction.transaction_date >= date_from)
    if date_to is not None:
        conditions.append(Transaction.transaction_date <= date_to)
    if account_id is not None:
        conditions.append(Transaction.account_id == account_id)
    if category_id is not None:
        conditions.append(Transaction.category_id == category_id)
    if vendor_id is not None:
        conditions.append(Transaction.merchant_id == vendor_id)
    if project_id is not None:
        conditions.append(Transaction.project_id == project_id)
    if tag_id is not None:
        conditions.append(Transaction.tags.any(Tag.id == tag_id))
    if needs_review is not None:
        conditions.append(Transaction.needs_review.is_(needs_review))
    if is_business is not None:
        conditions.append(Transaction.is_business.is_(is_business))
    if amount_min is not None:
        conditions.append(Transaction.amount >= amount_min)
    if amount_max is not None:
        conditions.append(Transaction.amount <= amount_max)
    return conditions


def _country_condition(code: str, default_country: str | None):
    """Match a transaction's **resolved** country — the same precedence the
    spend-by-location map uses (geo.country_for): the transaction's own country,
    else its vendor's, else the household default, else inferred from the currency.
    Without this, a drill-down from the map (which attributes by the resolved
    country) would only match the rare rows with a *stored* country and look empty.
    Uses subqueries on merchant_id so no Vendor join is needed."""
    from sqlalchemy import and_, or_

    from app.services import geo

    code = code.strip().upper()[:2]
    no_txn = Transaction.country.is_(None)
    vendor_is_code = Transaction.merchant_id.in_(select(Vendor.id).where(Vendor.country == code))
    # "no vendor country" = no vendor, or a vendor that has no country set.
    has_vendor_country = Transaction.merchant_id.in_(select(Vendor.id).where(Vendor.country.is_not(None)))
    no_vendor_country = ~has_vendor_country

    clauses = [Transaction.country == code, and_(no_txn, vendor_is_code)]
    default = (default_country or "").strip().upper()[:2] or None
    if default is not None:
        # A default vendor country, when set, beats the currency guess for any row
        # with no country of its own (txn or vendor).
        if default == code:
            clauses.append(and_(no_txn, no_vendor_country))
    else:
        # No default → fall back to the currency→country guess for those rows.
        currencies = [c for c, cc in geo.CURRENCY_COUNTRY.items() if cc == code]
        if currencies:
            clauses.append(and_(no_txn, no_vendor_country, Transaction.currency.in_(currencies)))
    return or_(*clauses)


def _search_conditions(
    *,
    country: str | None,
    uncategorised: bool | None,
    search: str | None,
    default_country: str | None = None,
) -> list:
    """The filters that need normalisation/special-casing (country, uncategorised, search)."""
    from sqlalchemy import or_

    conditions: list = []
    if country:
        # Drill-down from the "Spending by location" card — match the *resolved*
        # country (txn → vendor → default → currency), not just a stored code.
        conditions.append(_country_condition(country, default_country))
    if uncategorised is not None:
        # "Uncategorised" = no category assigned. This is distinct from
        # needs_review (a flag set on low-confidence/PDF imports): a transaction
        # can be uncategorised without being flagged, and vice versa.
        conditions.append(
            Transaction.category_id.is_(None) if uncategorised
            else Transaction.category_id.is_not(None)
        )
    if search:
        like = f"%{search}%"
        conditions.append(
            or_(Transaction.description_raw.ilike(like), Transaction.merchant_raw.ilike(like))
        )
    return conditions


def build_transaction_filters(
    *,
    transaction_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    account_id: int | None = None,
    category_id: int | None = None,
    vendor_id: int | None = None,
    project_id: int | None = None,
    tag_id: int | None = None,
    country: str | None = None,
    needs_review: bool | None = None,
    uncategorised: bool | None = None,
    is_business: bool | None = None,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
    search: str | None = None,
    account_ids: set[int] | None = None,
    include_archived: bool = False,
    default_country: str | None = None,
) -> list:
    """Build the SQLAlchemy filter list shared by the transactions list endpoint
    and the CSV export, so "export" always matches "what you see".

    ``account_ids`` is the visibility scope (None = unrestricted); it is applied
    in addition to the explicit ``account_id`` UI filter. Archived (aged-out)
    transactions are excluded unless ``include_archived`` (backlog #78)."""
    conditions: list = [*account_scope_condition(account_ids), *archived_condition(include_archived)]
    conditions.extend(
        _equality_conditions(
            transaction_id=transaction_id,
            date_from=date_from,
            date_to=date_to,
            account_id=account_id,
            category_id=category_id,
            vendor_id=vendor_id,
            project_id=project_id,
            tag_id=tag_id,
            needs_review=needs_review,
            is_business=is_business,
            amount_min=amount_min,
            amount_max=amount_max,
        )
    )
    conditions.extend(
        _search_conditions(
            country=country, uncategorised=uncategorised, search=search, default_country=default_country
        )
    )
    return conditions


def _name_maps(db: Session) -> tuple[dict, dict, dict, dict]:
    categories = {c.id: c.name for c in db.scalars(select(Category)).all()}
    projects = {p.id: p.name for p in db.scalars(select(Project)).all()}
    accounts = {a.id: a.name for a in db.scalars(select(Account)).all()}
    vendors = {
        v.id: (v.display_name or v.canonical_name) for v in db.scalars(select(Vendor)).all()
    }
    return categories, projects, accounts, vendors


def transactions_csv(db: Session, conditions: list) -> str:
    base_currency = settings_service.get_base_currency(db)
    categories, projects, accounts, vendors = _name_maps(db)

    stmt = select(Transaction)
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = (
        stmt.options(selectinload(Transaction.tags))
        .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        .limit(MAX_EXPORT_ROWS)
    )
    rows = db.scalars(stmt).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(TRANSACTION_HEADERS)
    for t in rows:
        writer.writerow(
            [
                t.transaction_date.isoformat() if t.transaction_date else "",
                t.posted_date.isoformat() if t.posted_date else "",
                t.description_raw or "",
                t.merchant_raw or (vendors.get(t.merchant_id) or ""),
                t.amount,
                t.currency,
                t.base_amount if t.base_amount is not None else "",
                base_currency,
                t.direction,
                categories.get(t.category_id, ""),
                projects.get(t.project_id, ""),
                accounts.get(t.account_id, ""),
                ", ".join(tag.name for tag in t.tags),
                t.is_split,
                t.is_transfer,
                t.is_income,
                t.is_business,
                t.vat_amount if t.vat_amount is not None else "",
                t.needs_review,
                t.review_reason or "",
            ]
        )
    return buf.getvalue()


def category_breakdown_csv(db: Session, month: date, *, account_ids: set[int] | None = None) -> str:
    """The data behind the dashboard "Spending by category" chart for a month."""
    rows = dashboard_service.category_breakdown(db, month, account_ids=account_ids)
    base_currency = settings_service.get_base_currency(db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["category", "total", "currency", "transactions"])
    for r in rows:
        writer.writerow([r["name"], r["total"], base_currency, r["count"]])
    return buf.getvalue()


def monthly_series_csv(db: Session, month: date, months: int, *, account_ids: set[int] | None = None) -> str:
    """The data behind the dashboard "Trends" sparklines (spend/income/net)."""
    series = monthly_series(db, month, months=months, account_ids=account_ids)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["month", "spend", "income", "net", "currency"])
    for point in series["months"]:
        writer.writerow(
            [point["month"], point["spend"], point["income"], point["net"], series["currency"]]
        )
    return buf.getvalue()
