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

Pace (additive): alongside the total-vs-cap status, a **pace** signal compares
spend against the PRORATED expectation for the elapsed fraction of the period
(e.g. half-way through a monthly budget, ~50% of the cap is "on track"). This
never changes the over/warn/ok semantics — it only adds fields.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

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


def _split_contribution(txn: Transaction, budget: Budget) -> Decimal | None:
    """Positive spend from a split transaction's matching parts, or None if none
    of its parts match the budget."""
    part = Decimal("0.00")
    matched = False
    for split in txn.splits:
        if _split_matches(split, budget):
            base = split_service.split_base_amount(txn, split)
            if base is not None:
                part += -base
                matched = True
    return part if matched else None


def _txn_contribution(txn: Transaction, budget: Budget) -> Decimal | None:
    """Positive spend this transaction contributes to the budget, or None if it
    doesn't count (mirrors total/category/project, split-aware)."""
    if budget.category_id is None and budget.project_id is None:
        # Total budget: the whole transaction counts.
        return -(txn.base_amount or Decimal("0"))
    if txn.is_split and txn.splits:
        return _split_contribution(txn, budget)
    if _txn_matches(txn, budget):
        return -(txn.base_amount or Decimal("0"))
    return None


def _spendable_select(start: date, end: date, account_ids: set[int] | None):
    """The windowed spendable-debit query shared by every budget calculation.

    ``selectinload(Transaction.splits)`` eager-loads each transaction's splits in
    ONE extra query instead of a lazy SELECT per split transaction (backlog #16
    N+1), so split-aware allocation costs a constant number of round-trips.
    """
    return (
        select(Transaction)
        .options(selectinload(Transaction.splits))
        .where(
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
            Transaction.is_transfer.is_(False),
            Transaction.is_duplicate.is_(False),
            Transaction.base_amount.is_not(None),
            Transaction.base_amount < 0,  # money out
            *account_scope_condition(account_ids),
            *archived_condition(),
        )
    )


def _spent_from_txns(
    txns: list[Transaction], start: date, end: date, budget: Budget
) -> Decimal:
    """Spend (positive number) this budget draws from already-loaded ``txns``.

    ``txns`` may span a wider range than [start, end) (``summary`` loads the union
    window once), so we re-apply the same half-open date filter the SQL uses.
    """
    total = Decimal("0.00")
    for txn in txns:
        if txn.transaction_date < start or txn.transaction_date >= end:
            continue
        contrib = _txn_contribution(txn, budget)
        if contrib is not None:
            total += contrib
    return total


def _spent(
    db: Session, start: date, end: date, budget: Budget, *, account_ids: set[int] | None = None
) -> Decimal:
    """Spend (positive number) against this budget over [start, end)."""
    txns = db.scalars(_spendable_select(start, end, account_ids)).all()
    return _spent_from_txns(list(txns), start, end, budget)


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
    if spent >= amount:
        # Spending the full budget (100%) is over the limit, not merely "warn".
        return "over"
    pct = (spent / amount) * 100
    if threshold is not None and pct >= threshold:
        return "warn"
    return "ok"


# Spend within ±5% of the cap around the prorated expectation counts as "on
# track" — a small band so day-to-day lumpiness doesn't flip the signal.
_PACE_TOLERANCE = Decimal("0.05")
_CENTS = Decimal("0.01")


def elapsed_fraction(start: date, end: date, ref: date) -> Decimal:
    """Fraction (0..1) of the period ``[start, end)`` elapsed as of ``ref``.

    ``ref`` before the window → 0 (period not started); on/after the window → 1
    (period ended → full period). A zero/negative-length window is treated as
    fully elapsed to guard against divide-by-zero. ``ref`` counts as a day
    already under way, so day 15 of a 30-day period → 15/30.
    """
    total_days = (end - start).days
    if total_days <= 0:
        return Decimal("1")
    if ref < start:
        return Decimal("0")
    if ref >= end:
        return Decimal("1")
    frac = Decimal((ref - start).days + 1) / Decimal(total_days)
    return frac if frac < 1 else Decimal("1")


def _pace_status(spent: Decimal, expected: Decimal, cap: Decimal) -> str:
    """Spend relative to the prorated expectation: ``ahead`` = spending faster
    than the elapsed period (over pace), ``behind`` = under the elapsed pace,
    ``on_track`` = within a ±5%-of-cap band."""
    if cap <= 0:
        return "on_track"
    tol = cap * _PACE_TOLERANCE
    if spent > expected + tol:
        return "ahead"
    if spent < expected - tol:
        return "behind"
    return "on_track"


def _pace_fields(spent: Decimal, cap: Decimal, start: date, end: date, ref: date) -> dict:
    """Additive pace signal for the budget summary (see module docstring)."""
    frac = elapsed_fraction(start, end, ref)
    expected = (cap * frac).quantize(_CENTS)
    return {
        "elapsed_fraction": float(round(frac, 4)),
        "pace_expected": str(expected),
        # Prorated headroom: positive = under the elapsed pace, negative = over.
        "pace_remaining": str((expected - spent).quantize(_CENTS)),
        "pace_status": _pace_status(spent, expected, cap),
    }


def _eval_window(budget: Budget, ref: date, annual: bool) -> tuple[date, date, Decimal]:
    """The [start, end) window and the cap to compare against. ``annual`` evaluates
    the whole calendar year and annualises the cap (amount × periods-per-year).

    A ``custom`` budget already covers a single fixed span (its own start/end),
    so it does not tile a year the way a weekly/monthly/quarterly cap does. For it
    the annualised cap is just the one-off amount (×1); to keep the cap and the
    spend window the SAME duration we therefore keep the spend window on the
    custom period itself instead of stretching it across the whole calendar year
    (which would leave a ×1 cap measured against a full year of spend and report a
    false over-budget). Weekly/monthly/quarterly/yearly are unchanged: their caps
    scale by periods-per-year and are compared against the whole year.
    """
    if annual:
        period = budget.period if budget.period in PERIODS else "monthly"
        cap = Decimal(budget.amount) * _PERIODS_PER_YEAR.get(period, 12)
        if period == "custom":
            # Cap is ×1, so keep the spend window on the custom period to match it.
            start, end = period_bounds(budget, ref)
            return start, end, cap
        return date(ref.year, 1, 1), date(ref.year + 1, 1, 1), cap
    start, end = period_bounds(budget, ref)
    return start, end, Decimal(budget.amount)


def _status_dict(
    budget: Budget, start: date, end: date, amount: Decimal, spent: Decimal, ref: date, currency: str
) -> dict:
    """Assemble one budget's summary row from an already-computed ``spent``."""
    remaining = amount - spent
    percent = float((spent / amount) * 100) if amount > 0 else 0.0
    return {
        "budget_id": budget.id,
        "name": budget.name,
        "category_id": budget.category_id,
        "project_id": budget.project_id,
        "period": budget.period,
        "currency": currency,
        "amount": str(amount),
        "spent": str(spent),
        "remaining": str(remaining),
        "percent": round(percent, 1),
        "status": _status(spent, amount, budget.alert_threshold_percent),
        "alert_threshold_percent": budget.alert_threshold_percent,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        **_pace_fields(spent, amount, start, end, ref),
    }


def status_for(
    db: Session, budget: Budget, ref: date, *, account_ids: set[int] | None = None, annual: bool = False
) -> dict:
    """Compute one budget's spend/remaining/percent/status for ``ref``'s period
    (or the whole year when ``annual``, comparing against the annualised cap)."""
    start, end, amount = _eval_window(budget, ref, annual)
    spent = _spent(db, start, end, budget, account_ids=account_ids)
    currency = settings_service.get_base_currency(db)
    return _status_dict(budget, start, end, amount, spent, ref, currency)


def summary(
    db: Session, ref: date, *, account_ids: set[int] | None = None, annual: bool = False
) -> list[dict]:
    """Status for every *household* budget (spec §24.9 GET /api/budgets/summary).

    Child-owned budgets (``owner_user_id`` set) are a kid's-allowance concern and
    are surfaced only on the child's allowance view, so they're excluded here.

    The spendable transactions are fetched ONCE over the union of every budget's
    window and filtered per budget in Python, instead of re-scanning the table
    (and lazy-loading splits) once per budget (backlog #16 N+1). Each budget's own
    window and category/project scope are still applied, so the numbers are
    identical to calling :func:`status_for` per budget.
    """
    budgets = db.scalars(
        select(Budget).where(Budget.owner_user_id.is_(None)).order_by(Budget.name)
    ).all()
    if not budgets:
        return []

    windows = [_eval_window(b, ref, annual) for b in budgets]
    union_start = min(start for start, _end, _amount in windows)
    union_end = max(end for _start, end, _amount in windows)
    txns = list(db.scalars(_spendable_select(union_start, union_end, account_ids)).all())
    currency = settings_service.get_base_currency(db)

    rows: list[dict] = []
    for budget, (start, end, amount) in zip(budgets, windows, strict=True):
        spent = _spent_from_txns(txns, start, end, budget)
        rows.append(_status_dict(budget, start, end, amount, spent, ref, currency))
    return rows


def budget_transactions(
    db: Session, budget: Budget, ref: date, *, account_ids: set[int] | None = None, annual: bool = False
) -> list[dict]:
    """The transactions counting toward this budget in the window (drill-down).

    Mirrors :func:`_spent`'s matching (total/category/project, split-aware), and
    reports each transaction's *contributing* base amount (positive)."""
    start, end, _ = _eval_window(budget, ref, annual)
    txns = db.scalars(
        _spendable_select(start, end, account_ids).order_by(
            Transaction.transaction_date.desc(), Transaction.id.desc()
        )
    ).all()

    out: list[dict] = []
    for txn in txns:
        contrib = _txn_contribution(txn, budget)
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
