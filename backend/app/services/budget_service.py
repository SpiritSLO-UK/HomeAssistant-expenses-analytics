"""Budget calculations (spec §19, §12.14).

A budget caps spend over a period. Three flavours (spec §19.1):
  - **category** budget: ``category_id`` set,
  - **project** budget: ``project_id`` set,
  - **total** budget: neither set — caps all spend.

Spend is computed in the household **base currency** from each transaction's
``base_amount`` (backlog #29), excluding transfers/duplicates, debits only.
Split transactions contribute per-part to the matching category/project (spec
§37.4), consistent with the dashboard.

Status (spec §19.2): ``over`` when spent exceeds the budget, ``warn`` at/above
the alert threshold (default 80%), else ``ok``.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Budget, Transaction
from app.services import settings_service, split_service
from app.services.dashboard_service import month_bounds
from app.services.scope import account_scope_condition, archived_condition

PERIODS = {"weekly", "monthly", "quarterly", "yearly", "custom"}
# How many of each budget period fit in a year — used to annualise the cap for
# the Budgets "This year" view.
_PERIODS_PER_YEAR = {"weekly": 52, "monthly": 12, "quarterly": 4, "yearly": 1, "custom": 1}


def period_bounds(budget: Budget, ref: date) -> tuple[date, date]:
    """Return [start, end) for the budget's current period around ``ref``.

    ``custom`` uses the budget's own start/end dates (end is inclusive in the
    model, so we add a day for the half-open interval); a missing custom bound
    falls back to a very wide range so the budget still totals something.
    """
    period = budget.period if budget.period in PERIODS else "monthly"
    if period == "monthly":
        return month_bounds(ref)
    if period == "weekly":
        monday = ref - timedelta(days=ref.weekday())
        return monday, monday + timedelta(days=7)
    if period == "yearly":
        return date(ref.year, 1, 1), date(ref.year + 1, 1, 1)
    if period == "quarterly":
        q_start_month = 3 * ((ref.month - 1) // 3) + 1
        start = date(ref.year, q_start_month, 1)
        end_month = q_start_month + 3
        end = date(ref.year + 1, 1, 1) if end_month > 12 else date(ref.year, end_month, 1)
        return start, end
    # custom
    start = budget.start_date or date.min
    end = (budget.end_date + timedelta(days=1)) if budget.end_date else date.max
    return start, end


def _spent(
    db: Session, start: date, end: date, budget: Budget, *, account_ids: set[int] | None = None
) -> Decimal:
    """Spend (positive number) against this budget over [start, end)."""
    txns = db.scalars(
        select(Transaction).where(
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
            Transaction.is_transfer.is_(False),
            Transaction.is_duplicate.is_(False),
            Transaction.base_amount.is_not(None),
            Transaction.base_amount < 0,  # money out
            *account_scope_condition(account_ids),
            *archived_condition(),
        )
    ).all()

    total = Decimal("0.00")
    for txn in txns:
        if budget.category_id is None and budget.project_id is None:
            # Total budget: the whole transaction counts.
            total += -txn.base_amount
        elif txn.is_split and txn.splits:
            for split in txn.splits:
                if _split_matches(split, budget):
                    base = split_service.split_base_amount(txn, split)
                    if base is not None:
                        total += -base
        elif _txn_matches(txn, budget):
            total += -txn.base_amount
    return total


def _txn_matches(txn: Transaction, budget: Budget) -> bool:
    if budget.category_id is not None:
        return txn.category_id == budget.category_id
    if budget.project_id is not None:
        return txn.project_id == budget.project_id
    return False


def _split_matches(split, budget: Budget) -> bool:
    if budget.category_id is not None:
        return split.category_id == budget.category_id
    if budget.project_id is not None:
        return split.project_id == budget.project_id
    return False


def _status(spent: Decimal, amount: Decimal, threshold: int | None) -> str:
    if amount <= 0:
        return "ok"
    if spent > amount:
        return "over"
    pct = (spent / amount) * 100
    if threshold is not None and pct >= threshold:
        return "warn"
    return "ok"


def _eval_window(budget: Budget, ref: date, annual: bool) -> tuple[date, date, Decimal]:
    """The [start, end) window and the cap to compare against. ``annual`` evaluates
    the whole calendar year and annualises the cap (amount × periods-per-year)."""
    if annual:
        period = budget.period if budget.period in PERIODS else "monthly"
        cap = Decimal(budget.amount) * _PERIODS_PER_YEAR.get(period, 12)
        return date(ref.year, 1, 1), date(ref.year + 1, 1, 1), cap
    start, end = period_bounds(budget, ref)
    return start, end, Decimal(budget.amount)


def status_for(
    db: Session, budget: Budget, ref: date, *, account_ids: set[int] | None = None, annual: bool = False
) -> dict:
    """Compute one budget's spend/remaining/percent/status for ``ref``'s period
    (or the whole year when ``annual``, comparing against the annualised cap)."""
    start, end, amount = _eval_window(budget, ref, annual)
    spent = _spent(db, start, end, budget, account_ids=account_ids)
    remaining = amount - spent
    percent = float((spent / amount) * 100) if amount > 0 else 0.0
    return {
        "budget_id": budget.id,
        "name": budget.name,
        "category_id": budget.category_id,
        "project_id": budget.project_id,
        "period": budget.period,
        "currency": settings_service.get_base_currency(db),
        "amount": str(amount),
        "spent": str(spent),
        "remaining": str(remaining),
        "percent": round(percent, 1),
        "status": _status(spent, amount, budget.alert_threshold_percent),
        "alert_threshold_percent": budget.alert_threshold_percent,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }


def summary(
    db: Session, ref: date, *, account_ids: set[int] | None = None, annual: bool = False
) -> list[dict]:
    """Status for every *household* budget (spec §24.9 GET /api/budgets/summary).

    Child-owned budgets (``owner_user_id`` set) are a kid's-allowance concern and
    are surfaced only on the child's allowance view, so they're excluded here.
    """
    budgets = db.scalars(
        select(Budget).where(Budget.owner_user_id.is_(None)).order_by(Budget.name)
    ).all()
    return [status_for(db, b, ref, account_ids=account_ids, annual=annual) for b in budgets]


def budget_transactions(
    db: Session, budget: Budget, ref: date, *, account_ids: set[int] | None = None, annual: bool = False
) -> list[dict]:
    """The transactions counting toward this budget in the window (drill-down).

    Mirrors :func:`_spent`'s matching (total/category/project, split-aware), and
    reports each transaction's *contributing* base amount (positive)."""
    start, end, _ = _eval_window(budget, ref, annual)
    txns = db.scalars(
        select(Transaction)
        .where(
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
            Transaction.is_transfer.is_(False),
            Transaction.is_duplicate.is_(False),
            Transaction.base_amount.is_not(None),
            Transaction.base_amount < 0,
            *account_scope_condition(account_ids),
            *archived_condition(),
        )
        .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
    ).all()

    out: list[dict] = []
    for txn in txns:
        contrib: Decimal | None = None
        if budget.category_id is None and budget.project_id is None:
            contrib = -txn.base_amount
        elif txn.is_split and txn.splits:
            part = Decimal("0.00")
            matched = False
            for split in txn.splits:
                if _split_matches(split, budget):
                    base = split_service.split_base_amount(txn, split)
                    if base is not None:
                        part += -base
                        matched = True
            if matched:
                contrib = part
        elif _txn_matches(txn, budget):
            contrib = -txn.base_amount
        if contrib is not None:
            out.append(
                {
                    "id": txn.id,
                    "transaction_date": txn.transaction_date.isoformat(),
                    "description": txn.merchant_raw or txn.description_raw,
                    "amount": str(contrib),
                }
            )
    return out
