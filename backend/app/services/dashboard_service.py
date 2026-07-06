"""Dashboard calculations (spec §37).

Monthly summary and category/vendor breakdowns. Totals are in the household
**base currency** using each transaction's ``base_amount`` (backlog #29);
foreign transactions without an FX rate yet (``base_amount IS NULL``) are
excluded until a rate is supplied. Transfers and duplicates are excluded from
spend/income.

Splits (spec §37.4): when a transaction ``is_split`` its split parts drive the
**category** breakdown instead of the transaction's own category. The monthly
spend/income totals are unchanged by splitting because the split parts sum to
the transaction total by validation (spec §17.2).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import AIRequest, Category, Receipt, Statement, Transaction, Vendor
from app.services import geo, settings_service, split_service
from app.services.scope import account_scope_condition, archived_condition


def month_bounds(ref: date) -> tuple[date, date]:
    """First day of ref's month and first day of the next month (exclusive end)."""
    start = ref.replace(day=1)
    end = date(ref.year + 1, 1, 1) if ref.month == 12 else date(ref.year, ref.month + 1, 1)
    return start, end


def _spendable_conditions():
    """Transactions that count toward totals: not transfers/duplicates and with
    a known base-currency amount (spec §37.1; backlog #29)."""
    return [
        Transaction.is_transfer.is_(False),
        Transaction.is_duplicate.is_(False),
        Transaction.base_amount.is_not(None),
    ]


def summary(db: Session, ref: date, *, account_ids: set[int] | None = None) -> dict:
    start, end = month_bounds(ref)
    # Visible accounts + archived-exclusion apply to every figure and count here.
    base = [*account_scope_condition(account_ids), *archived_condition()]
    window = [
        Transaction.transaction_date >= start,
        Transaction.transaction_date < end,
        *_spendable_conditions(),
        *base,
    ]

    # One conditional-aggregate pass for spend (money out) and income (money in),
    # both in base currency (backlog #29). A base_amount of exactly 0 counts as
    # neither, matching the strict < 0 / > 0 filters this replaced.
    spend, income = db.execute(
        select(
            func.coalesce(
                func.sum(case((Transaction.base_amount < 0, -Transaction.base_amount), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((Transaction.base_amount > 0, Transaction.base_amount), else_=0)), 0
            ),
        ).where(*window)
    ).one()
    spend = spend or Decimal("0")
    income = income or Decimal("0")

    # One conditional-count pass for the scoped tallies (counts are scoped like the
    # figures above, so a member can't infer the volume of private activity).
    total_txns, uncategorised, review_count, needs_rate = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(case((Transaction.category_id.is_(None), 1), else_=0)), 0),
            func.coalesce(func.sum(case((Transaction.needs_review.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(case((Transaction.needs_rate.is_(True), 1), else_=0)), 0),
        ).where(*base)
    ).one()

    return {
        "month": start.isoformat(),
        "currency": settings_service.get_base_currency(db),
        "spend_this_month": str(spend),
        "income_this_month": str(income),
        "net_this_month": str(income - spend),
        "total_transactions": int(total_txns),
        "uncategorised_transactions": int(uncategorised),
        "review_items": int(review_count),
        "needs_rate": int(needs_rate),
    }


def _accumulate_split(
    txn: Transaction,
    totals: dict[int | None, Decimal],
    counts: dict[int | None, int],
) -> None:
    """Add a split transaction's spend to ``totals``/``counts`` by category.

    Each part is attributed to its own category using the parent's FX rate /
    ``base_amount`` (``split_service`` keeps the parts penny-exact against the
    parent). A transaction flagged split but carrying no split rows falls back to
    its own category, matching the pre-refactor behaviour.
    """
    if txn.splits:
        for split in txn.splits:
            base = split_service.split_base_amount(txn, split)
            if base is None:
                continue
            totals[split.category_id] += -base
            counts[split.category_id] += 1
    else:
        totals[txn.category_id] += -(txn.base_amount or Decimal("0"))
        counts[txn.category_id] += 1


def category_breakdown(db: Session, ref: date, *, account_ids: set[int] | None = None) -> list[dict]:
    """Spend per category for the month, in base currency (positive = money out).

    Split-aware (spec §37.4): a split transaction contributes each of its parts
    to that part's category; a non-split transaction contributes its whole
    ``base_amount`` to its own category.

    The bulk (non-split) spend rolls up in a single ``GROUP BY category`` SQL
    pass (OPT-1); the far fewer split transactions stay in Python so each part's
    penny-exact base-currency share (``split_service``) is preserved unchanged.
    """
    start, end = month_bounds(ref)
    scope = [
        Transaction.transaction_date >= start,
        Transaction.transaction_date < end,
        Transaction.base_amount < 0,  # spend only (money out)
        *_spendable_conditions(),
        *account_scope_condition(account_ids),
        *archived_condition(),
    ]

    totals: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    counts: dict[int | None, int] = defaultdict(int)

    # Bulk path: non-split spend aggregated in the database.
    for cid, total, count in db.execute(
        select(Transaction.category_id, func.sum(-Transaction.base_amount), func.count())
        .where(*scope, Transaction.is_split.is_(False))
        .group_by(Transaction.category_id)
    ).all():
        totals[cid] += total
        counts[cid] += int(count)

    # Split path: eager-load parts (few rows) so split-base allocation + FX stay
    # exactly as before — one place, no N+1 (CR-FEAT-5).
    split_txns = db.scalars(
        select(Transaction)
        .where(*scope, Transaction.is_split.is_(True))
        .options(selectinload(Transaction.splits))
    ).all()
    for txn in split_txns:
        _accumulate_split(txn, totals, counts)

    cats = {c.id: c for c in db.scalars(select(Category)).all()}
    rows = [
        {
            "category_id": cid,
            "name": cats[cid].name if cid in cats else "Uncategorised",
            "colour": cats[cid].colour if cid in cats else None,
            "total": str(total),
            "count": counts[cid],
        }
        for cid, total in totals.items()
    ]
    rows.sort(key=lambda r: Decimal(r["total"]), reverse=True)
    return rows


def vendor_breakdown(db: Session, ref: date, limit: int = 10, *, account_ids: set[int] | None = None) -> list[dict]:
    """Top vendors by spend for the month, in base currency."""
    start, end = month_bounds(ref)
    rows = db.execute(
        select(
            Transaction.merchant_id,
            Vendor.canonical_name,
            func.sum(-Transaction.base_amount).label("total"),
            func.count().label("txn_count"),
        )
        .join(Vendor, Vendor.id == Transaction.merchant_id, isouter=True)
        .where(
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
            Transaction.base_amount < 0,
            Transaction.merchant_id.is_not(None),
            *_spendable_conditions(),
            *account_scope_condition(account_ids),
            *archived_condition(),
        )
        .group_by(Transaction.merchant_id, Vendor.canonical_name)
        .order_by(func.sum(-Transaction.base_amount).desc())
        .limit(limit)
    ).all()

    return [
        {
            "vendor_id": r.merchant_id,
            "name": r.canonical_name or "Unknown",
            "total": str(r.total),
            "count": int(r.txn_count),
        }
        for r in rows
    ]


def country_breakdown(db: Session, ref: date, *, account_ids: set[int] | None = None) -> list[dict]:
    """Spend by country for the month (base currency). A transaction's country is
    its vendor's country if set, otherwise inferred from the currency (geo.py).
    Aggregated in Python because the country is a vendor-or-currency fallback."""
    start, end = month_bounds(ref)
    rows = db.execute(
        select(-Transaction.base_amount, Transaction.currency, Transaction.country, Vendor.country)
        .join(Vendor, Vendor.id == Transaction.merchant_id, isouter=True)
        .where(
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
            Transaction.base_amount < 0,
            *_spendable_conditions(),
            *account_scope_condition(account_ids),
            *archived_condition(),
        )
    ).all()

    default_country = settings_service.get_default_vendor_country(db)
    buckets: dict[str, dict] = defaultdict(lambda: {"total": Decimal("0"), "count": 0})
    for amount, currency, txn_country, vendor_country in rows:
        code = geo.country_for(currency, vendor_country, txn_country, default_country) or "??"
        buckets[code]["total"] += Decimal(amount)
        buckets[code]["count"] += 1

    out = [
        {
            "country_code": None if code == "??" else code,
            "name": "Unknown" if code == "??" else geo.name(code),
            "flag": "\U0001F3F3️" if code == "??" else geo.flag(code),
            "total": str(b["total"].quantize(Decimal("0.01"))),
            "count": b["count"],
        }
        for code, b in buckets.items()
    ]
    out.sort(key=lambda x: Decimal(x["total"]), reverse=True)
    return out


# --- Processing stats (backlog: "status of files uploaded/processed, AI vs local") ---

_CLOUD_MODES = ("cloud_manual", "cloud_auto")


def _count(db: Session, model: type, *conditions) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def _receipt_stats(db: Session) -> tuple[int, int, int]:
    """(total, processed, failed) receipt counts in one grouped pass."""
    total, processed, failed = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(case((Receipt.ocr_status == "processed", 1), else_=0)), 0),
            func.coalesce(func.sum(case((Receipt.ocr_status == "failed", 1), else_=0)), 0),
        )
    ).one()
    return int(total), int(processed), int(failed)


def _ai_stats(db: Session) -> dict:
    """AI-request tallies (total/status/cloud) plus average turnaround, computed
    in a single conditional-aggregate pass instead of ~5 counts + a full
    timestamp pull. Turnaround is ``completed_at - created_at`` in seconds via
    SQLite's ``julianday``; ``None`` until at least one call has completed."""
    duration_secs = (
        func.julianday(AIRequest.completed_at) - func.julianday(AIRequest.created_at)
    ) * 86400.0
    total, completed, failed, pending, cloud, avg_secs = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(case((AIRequest.status == "completed", 1), else_=0)), 0),
            func.coalesce(func.sum(case((AIRequest.status == "failed", 1), else_=0)), 0),
            func.coalesce(func.sum(case((AIRequest.status == "pending", 1), else_=0)), 0),
            func.coalesce(func.sum(case((AIRequest.privacy_mode.in_(_CLOUD_MODES), 1), else_=0)), 0),
            func.avg(
                case(
                    (
                        and_(AIRequest.status == "completed", AIRequest.completed_at.is_not(None)),
                        duration_secs,
                    )
                )
            ),
        )
    ).one()
    return {
        "total": int(total),
        "completed": int(completed),
        "failed": int(failed),
        "pending": int(pending),
        "cloud": int(cloud),
        "avg_seconds": round(avg_secs, 2) if avg_secs is not None else None,
    }


def processing_stats(db: Session) -> dict:
    """A pipeline-status snapshot for the dashboard's processing card: how many
    files/transactions were imported, receipt OCR progress, and how many enrichment
    calls went through AI (cloud vs local) with the average AI turnaround. These are
    system/processing metrics, so they are household-wide (not account-scoped)."""
    statements_imported = _count(db, Statement, Statement.status == "imported")
    transactions_imported = _count(db, Transaction)

    receipts_total, receipts_processed, receipts_failed = _receipt_stats(db)
    receipts_pending = receipts_total - receipts_processed - receipts_failed

    ai = _ai_stats(db)
    ai_local = ai["total"] - ai["cloud"]

    # Per-task tally (classify_transaction, parse_receipt, …) for the breakdown.
    by_task = {
        task: int(n)
        for task, n in db.execute(
            select(AIRequest.task_type, func.count()).group_by(AIRequest.task_type)
        ).all()
    }

    return {
        "statements_imported": statements_imported,
        "transactions_imported": transactions_imported,
        "receipts_total": receipts_total,
        "receipts_processed": receipts_processed,
        "receipts_failed": receipts_failed,
        "receipts_pending": receipts_pending,
        "ai_total": ai["total"],
        "ai_completed": ai["completed"],
        "ai_failed": ai["failed"],
        "ai_pending": ai["pending"],
        "ai_cloud": ai["cloud"],
        "ai_local": ai_local,
        "ai_avg_seconds": ai["avg_seconds"],
        "ai_by_task": by_task,
    }
