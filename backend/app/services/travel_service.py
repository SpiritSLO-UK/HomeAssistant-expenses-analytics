"""Travel / spend-abroad analytics (backlog: holidays by country/currency).

We don't capture geolocation, so a transaction's **currency** is the signal for
"spent abroad": any spend in a currency other than the household base currency.
This groups that foreign spend by currency (with a friendly place label) and
clusters it into **trips** by date gaps, and can turn a detected trip into a
Project so it gains a budget + the usual project breakdowns.

Read-only over existing columns (no new model). Account-scoped + archived-aware
via the shared scope helpers, like every other aggregate.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, Transaction
from app.services import analytics_service, geo, settings_service
from app.services.household_service import get_or_create_default_household
from app.services.scope import account_scope_condition, archived_condition

# A new trip starts when the gap from the previous foreign-currency spend exceeds
# this many days (so a fortnight of euro spend = one "trip", not many).
DEFAULT_TRIP_GAP_DAYS = 14

# The currency → "where" mapping lives once, in geo.CURRENCY_COUNTRY (used by the
# spend-by-location map too); we reuse it here instead of a divergent copy. These
# few labels intentionally differ from geo.name() — shorter, friendlier trip
# labels where the full ISO country name is needlessly long.
_PLACE_LABEL_OVERRIDE = {"AE": "UAE", "HK": "Hong Kong"}


def place_for(currency: str) -> str:
    """Friendly place label for a currency (display only). Reuses geo's
    currency→country map; unknown currencies fall back to the code itself."""
    code = geo.CURRENCY_COUNTRY.get(currency.upper())
    if code is None:
        return currency.upper()
    return _PLACE_LABEL_OVERRIDE.get(code, geo.name(code))


def _foreign_spend(
    db: Session, account_ids: set[int] | None, *, ids: list[int] | None = None
) -> list[Transaction]:
    """Spendable foreign-currency transactions (money out), oldest first.

    Pass ``ids`` to restrict to specific rows while still enforcing every trip
    predicate (account scope, not archived/transfer/duplicate, foreign, money
    out) — i.e. to validate that caller-supplied ids are genuine trip rows."""
    base = settings_service.get_base_currency(db)
    conditions = [
        Transaction.currency != base,
        Transaction.base_amount.is_not(None),
        Transaction.base_amount < 0,  # money out
        Transaction.is_transfer.is_(False),
        Transaction.is_duplicate.is_(False),
        *account_scope_condition(account_ids),
        *archived_condition(),
    ]
    if ids is not None:
        conditions.append(Transaction.id.in_(ids))
    return list(
        db.scalars(
            select(Transaction).where(*conditions).order_by(Transaction.transaction_date)
        ).all()
    )


def by_currency(db: Session, *, account_ids: set[int] | None = None) -> dict:
    """Foreign spend grouped by currency, in both the original currency and the
    household base currency."""
    base = settings_service.get_base_currency(db)
    groups: dict[str, dict] = {}
    for t in _foreign_spend(db, account_ids):
        g = groups.setdefault(
            t.currency,
            {"original": Decimal("0.00"), "base": Decimal("0.00"), "count": 0, "dates": []},
        )
        g["original"] += -t.amount       # signed; negative = debit → positive spend
        g["base"] += -(t.base_amount or Decimal("0"))
        g["count"] += 1
        g["dates"].append(t.transaction_date)

    rows = [
        {
            "currency": cur,
            "place": place_for(cur),
            "original_total": str(g["original"]),
            "base_total": str(g["base"]),
            "count": g["count"],
            "first": min(g["dates"]).isoformat(),
            "last": max(g["dates"]).isoformat(),
        }
        for cur, g in groups.items()
    ]
    rows.sort(key=lambda r: Decimal(r["base_total"]), reverse=True)
    return {"base_currency": base, "currencies": rows}


def history(db: Session, *, account_ids: set[int] | None = None, months: int = 12) -> dict:
    """Foreign (travel) spend per month over the last ``months``, base currency,
    oldest first — for the over-time chart + period selector on the Travel page."""
    windows = analytics_service._month_windows(date.today(), max(1, months))
    txns = _foreign_spend(db, account_ids)
    series = []
    for start, end in windows:
        total = sum(
            (-(t.base_amount or Decimal("0")) for t in txns if start <= t.transaction_date < end),
            Decimal("0"),
        )
        series.append({"month": start.strftime("%Y-%m"), "total": str(total.quantize(Decimal("0.01")))})
    return {"currency": settings_service.get_base_currency(db), "months": series}


def _trip_txn(t: Transaction) -> dict:
    """Lightweight per-transaction view for the trip drill-down (spend shown
    positive, in both the original currency and base)."""
    return {
        "id": t.id,
        "transaction_date": t.transaction_date.isoformat(),
        "description": t.merchant_raw or t.description_raw,
        "amount": str(-t.amount),
        "currency": t.currency,
        "base_amount": str(-(t.base_amount or Decimal("0"))),
    }


def detect_trips(
    db: Session, *, account_ids: set[int] | None = None, gap_days: int = DEFAULT_TRIP_GAP_DAYS
) -> list[dict]:
    """Cluster foreign-currency spend into trips: a gap longer than ``gap_days``
    between consecutive foreign transactions starts a new trip. Newest first.
    Each trip carries its ``transactions`` so the UI can expand it (drill-down)."""
    trips: list[dict] = []
    current: dict | None = None
    prev_date = None
    for t in _foreign_spend(db, account_ids):
        if current is None or prev_date is None or (t.transaction_date - prev_date).days > gap_days:
            current = {
                "transaction_ids": [],
                "transactions": [],
                "currencies": set(),
                "base_total": Decimal("0.00"),
                "first": t.transaction_date,
                "last": t.transaction_date,
            }
            trips.append(current)
        current["transaction_ids"].append(t.id)
        current["transactions"].append(_trip_txn(t))
        current["currencies"].add(t.currency)
        current["base_total"] += -(t.base_amount or Decimal("0"))
        current["last"] = t.transaction_date
        prev_date = t.transaction_date

    out = []
    for tr in trips:
        currencies = sorted(tr["currencies"])
        places = sorted({place_for(c) for c in currencies})
        out.append(
            {
                "first": tr["first"].isoformat(),
                "last": tr["last"].isoformat(),
                "currencies": currencies,
                "places": places,
                "label": " / ".join(places),
                "base_total": str(tr["base_total"]),
                "base_currency": settings_service.get_base_currency(db),
                "transaction_count": len(tr["transaction_ids"]),
                "transaction_ids": tr["transaction_ids"],
                # Sorted newest-first within the trip, to match the list elsewhere.
                "transactions": sorted(
                    tr["transactions"], key=lambda x: x["transaction_date"], reverse=True
                ),
            }
        )
    out.sort(key=lambda r: r["last"], reverse=True)
    return out


def create_project_from_trip(
    db: Session,
    *,
    name: str,
    transaction_ids: list[int],
    budget_amount: Decimal | None = None,
    account_ids: set[int] | None = None,
) -> Project:
    """Create a Project from a detected trip and assign its (visible) transactions
    to it, dating the project from the assigned spend so a budget period fits."""
    # Resolve the passed ids to genuine, visible trip rows before creating the
    # project: this excludes archived rows and rejects ids that aren't actually
    # foreign-spend (trip) rows for the caller's accounts.
    txns = _foreign_spend(db, account_ids, ids=transaction_ids)
    if not txns:
        raise ValueError("No valid trip transactions to add.")

    household = get_or_create_default_household(db)
    project = Project(
        household_id=household.id,
        name=name.strip(),
        status="active",
        budget_amount=budget_amount,
    )
    db.add(project)
    db.flush()

    dates = []
    for t in txns:
        t.project_id = project.id
        dates.append(t.transaction_date)
    project.start_date = min(dates)
    project.end_date = max(dates)

    db.commit()
    db.refresh(project)
    return project
