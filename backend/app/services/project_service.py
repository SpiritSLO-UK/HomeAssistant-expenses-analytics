"""Project reporting (spec §18, §12.12).

Projects are first-class cost collectors (renovation, holiday, car, …). A
transaction belongs to a project when its ``project_id`` is the project, or —
for split transactions — when one of its split parts is (spec §37.5). As
everywhere, a **split transaction is driven by its splits**: only the parts
assigned to the project count, never the whole.

Spend is money-out in the household base currency (debits only), split-aware and
consistent with budgets (`budget_service`) and the dashboard.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Project, Transaction, TransactionSplit, Vendor
from app.services import analytics_service, settings_service, split_service
from app.services.scope import account_scope_condition, archived_condition


def _project_transactions(
    db: Session, project_id: int, *, account_ids: set[int] | None = None
) -> list[Transaction]:
    """Transactions touching the project (directly or via a split). The final
    fetch is account-scoped, so a split funded by a hidden private account is
    excluded by its parent transaction's account."""
    direct = db.scalars(
        select(Transaction.id).where(Transaction.project_id == project_id)
    ).all()
    via_split = db.scalars(
        select(TransactionSplit.transaction_id).where(TransactionSplit.project_id == project_id)
    ).all()
    ids = set(direct) | set(via_split)
    if not ids:
        return []
    return list(
        db.scalars(
            select(Transaction).where(
                Transaction.id.in_(ids),
                *account_scope_condition(account_ids),
                *archived_condition(),
            )
        ).all()
    )


def _accumulate_txn(
    txn: Transaction,
    project_id: int,
    by_cat: dict[int | None, Decimal],
    by_vendor: dict[int | None, Decimal],
) -> Decimal:
    """Add one transaction's project spend into ``by_cat``/``by_vendor`` (split-aware)
    and return the positive amount it contributed (``0`` if it contributed nothing)."""
    contributed = Decimal("0.00")
    if txn.is_split and txn.splits:
        for split in txn.splits:
            if split.project_id != project_id:
                continue
            base = split_service.split_base_amount(txn, split)
            if base is None or base >= 0:
                continue
            amt = -base
            contributed += amt
            by_cat[split.category_id] += amt
            by_vendor[txn.merchant_id] += amt
    elif txn.project_id == project_id and txn.base_amount is not None and txn.base_amount < 0:
        amt = -txn.base_amount
        contributed += amt
        by_cat[txn.category_id] += amt
        by_vendor[txn.merchant_id] += amt
    return contributed


def _accumulate(db: Session, project_id: int, *, account_ids: set[int] | None = None):
    """Return (spent, by_category, by_vendor, count, first_date, last_date)."""
    by_cat: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    by_vendor: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    spent = Decimal("0.00")
    count = 0
    dates: list = []

    for txn in _project_transactions(db, project_id, account_ids=account_ids):
        before = spent
        spent += _accumulate_txn(txn, project_id, by_cat, by_vendor)
        if spent != before:
            count += 1
            dates.append(txn.transaction_date)

    return spent, by_cat, by_vendor, count, (min(dates) if dates else None), (max(dates) if dates else None)


def history(db: Session, *, account_ids: set[int] | None = None, months: int = 12) -> dict:
    """Total project-attributed spend per month across all projects (split-aware,
    reusing the same accumulation as the project totals), oldest first — for the
    over-time chart + period selector on the Projects page."""
    windows = analytics_service._month_windows(date.today(), max(1, months))
    totals: dict[date, Decimal] = {start: Decimal("0.00") for start, _ in windows}
    sink_cat: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    sink_ven: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for project in db.scalars(select(Project)).all():
        for txn in _project_transactions(db, project.id, account_ids=account_ids):
            amt = _accumulate_txn(txn, project.id, sink_cat, sink_ven)  # split-aware contribution
            if amt <= 0:
                continue
            for start, end in windows:
                if start <= txn.transaction_date < end:
                    totals[start] += amt
                    break
    series = [
        {"month": start.strftime("%Y-%m"), "total": str(totals[start].quantize(Decimal("0.01")))}
        for start, _ in windows
    ]
    return {"currency": settings_service.get_base_currency(db), "months": series}


def _named_breakdown(rows: dict, names: dict[int | None, str], fallback: str) -> list[dict]:
    out = [
        {"id": key, "name": names.get(key, fallback), "total": str(total)}
        for key, total in rows.items()
    ]
    out.sort(key=lambda r: Decimal(r["total"]), reverse=True)
    return out


def _budget_fields(project: Project, spent: Decimal) -> dict:
    budget = project.budget_amount
    if budget is None or budget <= 0:
        return {"budget": (str(budget) if budget is not None else None),
                "remaining": None, "percent": None}
    return {
        "budget": str(budget),
        "remaining": str(Decimal(budget) - spent),
        "percent": round(float((spent / Decimal(budget)) * 100), 1),
    }


def summary(db: Session, project: Project, *, account_ids: set[int] | None = None) -> dict:
    """Full project report (spec §18.2): total, by-category, by-vendor, count,
    timeline, and budget progress when the project has a budget."""
    spent, by_cat, by_vendor, count, first, last = _accumulate(db, project.id, account_ids=account_ids)

    cats: dict[int | None, str] = {c.id: c.name for c in db.scalars(select(Category)).all()}
    vendors: dict[int | None, str] = {v.id: v.canonical_name for v in db.scalars(select(Vendor)).all()}

    return {
        "project_id": project.id,
        "name": project.name,
        "status": project.status,
        "currency": settings_service.get_base_currency(db),
        "spent": str(spent),
        **_budget_fields(project, spent),
        "transaction_count": count,
        "first_transaction": first.isoformat() if first else None,
        "last_transaction": last.isoformat() if last else None,
        "by_category": _named_breakdown(by_cat, cats, "Uncategorised"),
        "by_vendor": _named_breakdown(by_vendor, vendors, "Unknown"),
    }


def totals(db: Session, *, account_ids: set[int] | None = None) -> list[dict]:
    """One row per project for the dashboard "Project totals" card (spec §25.1)."""
    rows = []
    for project in db.scalars(select(Project).order_by(Project.name)).all():
        spent, *_ = _accumulate(db, project.id, account_ids=account_ids)
        rows.append(
            {
                "project_id": project.id,
                "name": project.name,
                "status": project.status,
                "currency": settings_service.get_base_currency(db),
                "spent": str(spent),
                **_budget_fields(project, spent),
            }
        )
    rows.sort(key=lambda r: Decimal(r["spent"]), reverse=True)
    return rows
