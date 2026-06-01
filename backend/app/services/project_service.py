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
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Project, Transaction, TransactionSplit, Vendor
from app.services import settings_service, split_service
from app.services.scope import account_scope_condition


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
                Transaction.id.in_(ids), *account_scope_condition(account_ids)
            )
        ).all()
    )


def _accumulate(db: Session, project_id: int, *, account_ids: set[int] | None = None):
    """Return (spent, by_category, by_vendor, count, first_date, last_date)."""
    by_cat: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    by_vendor: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    spent = Decimal("0.00")
    count = 0
    dates: list = []

    for txn in _project_transactions(db, project_id, account_ids=account_ids):
        contributed = False
        if txn.is_split and txn.splits:
            for split in txn.splits:
                if split.project_id != project_id:
                    continue
                base = split_service.split_base_amount(txn, split)
                if base is None or base >= 0:
                    continue
                amt = -base
                spent += amt
                by_cat[split.category_id] += amt
                by_vendor[txn.merchant_id] += amt
                contributed = True
        elif txn.project_id == project_id and txn.base_amount is not None and txn.base_amount < 0:
            amt = -txn.base_amount
            spent += amt
            by_cat[txn.category_id] += amt
            by_vendor[txn.merchant_id] += amt
            contributed = True
        if contributed:
            count += 1
            dates.append(txn.transaction_date)

    return spent, by_cat, by_vendor, count, (min(dates) if dates else None), (max(dates) if dates else None)


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

    cats = {c.id: c.name for c in db.scalars(select(Category)).all()}
    vendors = {v.id: v.canonical_name for v in db.scalars(select(Vendor)).all()}

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
