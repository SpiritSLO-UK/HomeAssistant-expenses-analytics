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
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

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
            select(Transaction)
            .options(selectinload(Transaction.splits))
            .where(
                Transaction.id.in_(ids),
                *account_scope_condition(account_ids),
                *archived_condition(),
            )
        ).all()
    )


def _all_project_transactions(
    db: Session, *, account_ids: set[int] | None = None
) -> list[Transaction]:
    """Every account-scoped, non-archived transaction touching *any* project
    (directly or via a split), splits eager-loaded — fetched in a small constant
    number of queries so multi-project reports (``totals``/``history``) avoid the
    N+1 of one fetch per project."""
    direct = db.scalars(
        select(Transaction.id).where(Transaction.project_id.is_not(None))
    ).all()
    via_split = db.scalars(
        select(TransactionSplit.transaction_id).where(TransactionSplit.project_id.is_not(None))
    ).all()
    ids = set(direct) | set(via_split)
    if not ids:
        return []
    return list(
        db.scalars(
            select(Transaction)
            .options(selectinload(Transaction.splits))
            .where(
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


def _txns_by_project(
    txns: list[Transaction],
) -> dict[int, list[Transaction]]:
    """Group already-fetched transactions by the project(s) they touch (directly
    or via a split part). One transaction may appear under several projects."""
    grouped: dict[int, list[Transaction]] = defaultdict(list)
    for txn in txns:
        seen: set[int] = set()
        if txn.project_id is not None:
            seen.add(txn.project_id)
        if txn.is_split:
            for split in txn.splits:
                if split.project_id is not None:
                    seen.add(split.project_id)
        for pid in seen:
            grouped[pid].append(txn)
    return grouped


def history(
    db: Session,
    *,
    account_ids: set[int] | None = None,
    months: int = 12,
    ref: date | None = None,
) -> dict:
    """Total project-attributed spend per month across all projects (split-aware,
    reusing the same accumulation as the project totals), oldest first — for the
    over-time chart + period selector on the Projects page.

    ``ref`` anchors the trailing window (default = today) so callers/tests can
    compute the series relative to a fixed date deterministically."""
    ref = ref or date.today()
    windows = analytics_service._month_windows(ref, max(1, months))
    totals: dict[date, Decimal] = {start: Decimal("0.00") for start, _ in windows}
    # month-start keyed lookup so bucketing is O(1) per txn, not O(windows).
    month_key: dict[tuple[int, int], date] = {(start.year, start.month): start for start, _ in windows}
    sink_cat: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    sink_ven: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    txns = _all_project_transactions(db, account_ids=account_ids)
    for project_id, project_txns in _txns_by_project(txns).items():
        for txn in project_txns:
            amt = _accumulate_txn(txn, project_id, sink_cat, sink_ven)  # split-aware contribution
            if amt <= 0:
                continue
            td = txn.transaction_date
            start = month_key.get((td.year, td.month))
            if start is not None:
                totals[start] += amt
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


def _run_rate(spent: Decimal, first: date | None, last: date | None) -> Decimal | None:
    """Average money-out per day over the elapsed spend history (``first``→``last``).

    ``None`` when there is nothing to extrapolate from: no spend, no history, or a
    single-day/zero-elapsed window (avoids divide-by-zero)."""
    if first is None or last is None or spent <= 0:
        return None
    elapsed = (last - first).days
    if elapsed <= 0:
        return None
    return spent / Decimal(elapsed)


def _exhaustion_date(remaining: Decimal, rate: Decimal, last: date) -> str | None:
    """Estimated point the budget is spent through, projecting ``rate`` forward from
    the last spend. Already overspent → the last spend date; no rate → ``None``."""
    if rate <= 0:
        return None
    if remaining <= 0:
        return last.isoformat()
    return (last + timedelta(days=int(remaining / rate))).isoformat()


def _forecast_total(rate: Decimal, spent: Decimal, first: date, end_date: date | None) -> Decimal | None:
    """Total spend forecast at ``rate`` across the planned window (``first``→
    ``end_date``); never below what's already spent. ``None`` without a usable
    ``end_date`` (no finish line to project a total to)."""
    if end_date is None or end_date < first:
        return None
    return max(rate * Decimal((end_date - first).days), spent)


def _forecast(
    *,
    budget: Decimal | None,
    spent: Decimal,
    first: date | None,
    last: date | None,
    end_date: date | None,
) -> dict | None:
    """Additive burn-down / run-rate forecast vs budget (spec §18.2). ``None`` when
    the project has no positive budget (forecast is skipped). Handles zero history
    and zero-elapsed time by degrading to a rate-less burn-down figure."""
    if budget is None or budget <= 0:
        return None
    budget = Decimal(budget)
    remaining = budget - spent  # burn-down: budget − spent (negative = overspent)
    rate = _run_rate(spent, first, last)

    forecast_total: Decimal | None = None
    exhaustion: str | None = None
    if rate is not None:
        exhaustion = _exhaustion_date(remaining, rate, last)  # last set when rate set
        forecast_total = _forecast_total(rate, spent, first, end_date)

    projected = forecast_total if forecast_total is not None else spent
    q = Decimal("0.01")
    return {
        "budget": str(budget.quantize(q)),
        "remaining": str(remaining.quantize(q)),
        "run_rate_per_day": None if rate is None else str(rate.quantize(q)),
        "forecast_total": None if forecast_total is None else str(forecast_total.quantize(q)),
        "on_track": projected <= budget,
        "exhaustion_date": exhaustion,
    }


def summary(db: Session, project: Project, *, account_ids: set[int] | None = None) -> dict:
    """Full project report (spec §18.2): total, by-category, by-vendor, count,
    timeline, budget progress and a run-rate/burn-down ``forecast`` when the
    project has a budget."""
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
        "forecast": _forecast(
            budget=project.budget_amount,
            spent=spent,
            first=first,
            last=last,
            end_date=project.end_date,
        ),
        "transaction_count": count,
        "first_transaction": first.isoformat() if first else None,
        "last_transaction": last.isoformat() if last else None,
        "by_category": _named_breakdown(by_cat, cats, "Uncategorised"),
        "by_vendor": _named_breakdown(by_vendor, vendors, "Unknown"),
    }


def totals(db: Session, *, account_ids: set[int] | None = None) -> list[dict]:
    """One row per project for the dashboard "Project totals" card (spec §25.1).

    Uses a single account-scoped fetch of project-touching transactions (splits
    eager-loaded) and assembles per-project spend in Python, so the query count
    is a small constant regardless of how many projects exist."""
    grouped = _txns_by_project(_all_project_transactions(db, account_ids=account_ids))
    spent_by_project: dict[int, Decimal] = {}
    _sink_cat: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    _sink_ven: dict[int | None, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for project_id, project_txns in grouped.items():
        spent = Decimal("0.00")
        for txn in project_txns:
            spent += _accumulate_txn(txn, project_id, _sink_cat, _sink_ven)
        spent_by_project[project_id] = spent

    rows = []
    for project in db.scalars(select(Project).order_by(Project.name)).all():
        spent = spent_by_project.get(project.id, Decimal("0.00"))
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
