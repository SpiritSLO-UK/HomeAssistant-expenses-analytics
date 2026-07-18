"""Savings accounts, balance snapshots and goals (spec §12.4; backlog #96, #91).

All money is kept as ``Decimal``. Totals are reported in the household base
currency: each account's balance is converted via the cached FX rate before
summing (SR-3), so a mixed-currency total is correct rather than added 1:1. A
foreign balance with no available rate is left out of the base total (mirroring a
transaction's ``needs_rate``); single-currency households are unaffected.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, SavingsBalance, SavingsGoal
from app.services import analytics_service, fx_service, settings_service
from app.services.household_service import get_or_create_default_household

SAVINGS_TYPE = "savings"
GOAL_STATUSES = {"active", "achieved", "archived"}
# Nullable goal fields an update may explicitly clear (set back to ``None``).
CLEARABLE_GOAL_FIELDS = {"target_date", "account_id"}
TWO_DP = Decimal("0.01")


# --- Accounts ----------------------------------------------------------------


def list_accounts(
    db: Session, *, owner_user_id: int | None = None, account_ids: set[int] | None = None
) -> list[Account]:
    stmt = select(Account).where(
        Account.account_type == SAVINGS_TYPE, Account.is_active.is_(True)
    )
    if owner_user_id is not None:
        stmt = stmt.where(Account.owner_user_id == owner_user_id)
    if account_ids is not None:  # visibility scope (shared vs private; #66/#82)
        stmt = stmt.where(Account.id.in_(account_ids))
    return list(db.scalars(stmt.order_by(Account.name)).all())


def create_account(db: Session, *, name: str, institution: str | None = None,
                    currency: str | None = None) -> Account:
    household = get_or_create_default_household(db)
    account = Account(
        household_id=household.id,
        name=name.strip(),
        institution=(institution or None),
        account_type=SAVINGS_TYPE,
        currency=(currency or settings_service.get_base_currency(db)).upper(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def get_savings_account(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None or account.account_type != SAVINGS_TYPE:
        raise ValueError("Not a savings account")
    return account


# --- Balance snapshots -------------------------------------------------------


def adjust_balance(db: Session, account_id: int, *, delta: Decimal,
                   note: str | None = None) -> SavingsBalance:
    """Deposit (positive delta) or withdraw (negative) by recording a new balance
    snapshot for today at ``latest + delta``. Reuses the snapshot history so the
    +/- control and manual balances live in one timeline."""
    current = latest_balance(db, account_id) or Decimal("0")
    new_balance = (current + Decimal(delta)).quantize(TWO_DP)
    return record_balance(db, account_id, as_of=date.today(), balance=new_balance, note=note)


def set_interest_rate(db: Session, account_id: int, rate: Decimal | None) -> Account:
    account = get_savings_account(db, account_id)
    account.interest_rate = (
        Decimal(rate).quantize(Decimal("0.001")) if rate is not None else None
    )
    db.commit()
    db.refresh(account)
    return account


def record_balance(db: Session, account_id: int, *, as_of: date, balance: Decimal,
                   note: str | None = None) -> SavingsBalance:
    account = get_savings_account(db, account_id)
    row = SavingsBalance(
        account_id=account.id,
        as_of_date=as_of,
        balance=Decimal(balance).quantize(TWO_DP),
        currency=account.currency,
        note=(note or None),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def balance_history(db: Session, account_id: int) -> list[SavingsBalance]:
    return list(
        db.scalars(
            select(SavingsBalance)
            .where(SavingsBalance.account_id == account_id)
            .order_by(SavingsBalance.as_of_date, SavingsBalance.id)
        ).all()
    )


def _snapshots_by_account(db: Session, account_ids: list[int]) -> dict[int, list[SavingsBalance]]:
    """All snapshots for ``account_ids`` grouped by account, each date-ordered, in
    ONE query (avoids the per-account N+1 of calling ``balance_history`` in a loop).
    Every requested account gets a list (empty if it has no snapshots)."""
    grouped: dict[int, list[SavingsBalance]] = {aid: [] for aid in account_ids}
    if not account_ids:
        return grouped
    rows = db.scalars(
        select(SavingsBalance)
        .where(SavingsBalance.account_id.in_(account_ids))
        .order_by(SavingsBalance.as_of_date, SavingsBalance.id)
    ).all()
    for row in rows:
        grouped[row.account_id].append(row)
    return grouped


def _latest_balances(db: Session, account_ids: list[int]) -> dict[int, Decimal]:
    """Latest snapshot balance per account (by date, ``id`` as tiebreak) in ONE
    query. Accounts with no snapshot are omitted from the result."""
    latest: dict[int, Decimal] = {}
    for account_id, snaps in _snapshots_by_account(db, account_ids).items():
        if snaps:  # ascending order → the last row is the newest
            latest[account_id] = Decimal(snaps[-1].balance)
    return latest


def _balance_as_of(snaps: list, end: date) -> Decimal | None:
    """The latest snapshot balance strictly before ``end`` (snaps are date-ordered)."""
    as_of_balance = None
    for snap in snaps:
        if snap.as_of_date < end:
            as_of_balance = Decimal(snap.balance)
        else:
            break
    return as_of_balance


def _month_total(db: Session, accounts: list, snaps: dict, end: date, base: str, on: date) -> Decimal:
    """Point-in-time total across accounts as of ``end``, converted to base
    (a foreign balance with no available rate is skipped)."""
    total = Decimal("0.00")
    for a in accounts:
        bal = _balance_as_of(snaps[a.id], end)
        if bal is None:
            continue
        converted = fx_service.convert_amount(db, bal, a.currency, base, on)
        if converted is not None:
            total += converted
    return total


def history(db: Session, *, account_ids: set[int] | None = None, months: int = 12) -> dict:
    """Total savings over time: each month's point-in-time total — the latest
    snapshot of every account as of that month's end — oldest first. Powers the
    Savings over-time chart + range selector (period epic)."""
    windows = analytics_service._month_windows(date.today(), max(1, months))
    base = settings_service.get_base_currency(db)
    today = date.today()
    accounts = list_accounts(db, account_ids=account_ids)
    snaps = _snapshots_by_account(db, [a.id for a in accounts])
    series = [
        {
            "month": start.strftime("%Y-%m"),
            "total": str(_month_total(db, accounts, snaps, end, base, today).quantize(TWO_DP)),
        }
        for start, end in windows
    ]
    return {"currency": base, "months": series}


def latest_balance(db: Session, account_id: int) -> Decimal | None:
    row = db.scalars(
        select(SavingsBalance)
        .where(SavingsBalance.account_id == account_id)
        .order_by(SavingsBalance.as_of_date.desc(), SavingsBalance.id.desc())
        .limit(1)
    ).first()
    return Decimal(row.balance) if row else None


def total_savings(
    db: Session, *, owner_user_id: int | None = None, account_ids: set[int] | None = None
) -> Decimal:
    """Sum of the latest snapshot of every savings account, converted to base (SR-3).
    A foreign balance with no available rate is skipped."""
    base = settings_service.get_base_currency(db)
    today = date.today()
    accounts = list_accounts(db, owner_user_id=owner_user_id, account_ids=account_ids)
    latest = _latest_balances(db, [a.id for a in accounts])
    total = Decimal("0.00")
    for account in accounts:
        bal = latest.get(account.id)
        if bal is not None:
            converted = fx_service.convert_amount(db, bal, account.currency, base, today)
            if converted is not None:
                total += converted
    return total


def account_to_dict(db: Session, account: Account) -> dict:
    history = balance_history(db, account.id)
    latest = latest_balance(db, account.id) if history else None
    rate = Decimal(account.interest_rate) if account.interest_rate is not None else None
    # Simple projection: one year of interest at the current balance + rate.
    projected = (
        (latest * rate / 100).quantize(TWO_DP) if latest is not None and rate is not None else None
    )
    return {
        "id": account.id,
        "name": account.name,
        "institution": account.institution,
        "currency": account.currency,
        "latest_balance": str(latest) if latest is not None else None,
        "balance_count": len(history),
        "interest_rate": str(rate) if rate is not None else None,
        "projected_annual_interest": str(projected) if projected is not None else None,
    }


COMPOUNDS_PER_YEAR = {"monthly": 12, "annual": 1}


def project_balance(
    account: Account, months: int, *, principal: Decimal, frequency: str = "monthly"
) -> Decimal:
    """Project ``principal`` forward ``months`` months at ``account.interest_rate``
    (an annual percentage from PR #42), compounding ``monthly`` or ``annual``.

    Pure and DB-free: interest accrues once per full compounding period that fits
    in the horizon, so the math stays exact ``Decimal`` (integer exponent) with no
    float rounding. Returns the projected balance quantized to 2dp; an account with
    no rate (or a non-positive horizon) yields ``principal`` unchanged.
    """
    if frequency not in COMPOUNDS_PER_YEAR:
        raise ValueError(f"frequency must be one of {sorted(COMPOUNDS_PER_YEAR)}")
    principal = Decimal(principal)
    if account.interest_rate is None or months <= 0:
        return principal.quantize(TWO_DP)
    per_year = COMPOUNDS_PER_YEAR[frequency]
    periods = months * per_year // 12  # whole compounding periods within the horizon
    rate_per_period = (Decimal(account.interest_rate) / 100) / per_year
    return (principal * (1 + rate_per_period) ** periods).quantize(TWO_DP)


# --- Goals -------------------------------------------------------------------


def goal_current(db: Session, goal: SavingsGoal) -> Decimal:
    """A linked goal tracks its account's latest balance; otherwise the manual
    ``current_amount``."""
    if goal.account_id is not None:
        bal = latest_balance(db, goal.account_id)
        if bal is not None:
            return bal
    return Decimal(goal.current_amount or 0)


# --- Deposit-rate / time-to-goal forecast ------------------------------------
#
# From a goal's balance-snapshot history we infer an average net-deposit rate
# (contributions minus withdrawals per ~30-day month) and, at that rate, project
# when the target is reached and whether that lands on/behind any ``target_date``.
# Additive to the goal summary — every state returns the same keys so callers can
# read ``forecast`` without changing existing fields.

FORECAST_PERIOD_DAYS = Decimal("30")  # a "month" for the reported rate/horizon


def _monthly_deposit_rate(snapshots: list[tuple[date, Decimal]]) -> Decimal | None:
    """Average net contribution per 30-day month across the snapshot window
    (``latest − earliest`` balance over the elapsed days). ``None`` when fewer
    than two snapshots span a positive number of days — no rate can be inferred."""
    if len(snapshots) < 2:
        return None
    (first_date, first_bal), (last_date, last_bal) = snapshots[0], snapshots[-1]
    elapsed_days = (last_date - first_date).days
    if elapsed_days <= 0:
        return None
    return (last_bal - first_bal) / Decimal(elapsed_days) * FORECAST_PERIOD_DAYS


def _forecast_result(state: str, *, monthly_rate: Decimal | None = None,
                     projected_date: date | None = None,
                     months_remaining: Decimal | None = None,
                     on_track: bool | None = None) -> dict:
    """Uniform forecast payload; unfilled parts stay ``None`` so the shape is
    stable across every ``state``."""
    return {
        "state": state,
        "monthly_deposit_rate": (
            str(monthly_rate.quantize(TWO_DP)) if monthly_rate is not None else None
        ),
        "projected_date": projected_date.isoformat() if projected_date else None,
        "months_remaining": (
            round(float(months_remaining), 1) if months_remaining is not None else None
        ),
        "on_track": on_track,
    }


_ON_TRACK_STATE = {True: "on_track", False: "behind", None: "projected"}


def forecast_goal(*, current: Decimal, target: Decimal,
                  snapshots: list[tuple[date, Decimal]],
                  target_date: date | None, today: date) -> dict:
    """Project when ``target`` is reached at the recent average deposit rate.

    Pure and DB-free. States: ``no_forecast`` (no target or no usable deposit
    history), ``achieved`` (already at/over target), ``not_progressing`` (net
    withdrawals — target recedes), and ``on_track`` / ``behind`` / ``projected``
    (has a date; on/behind is set only when the goal has a ``target_date``).
    """
    remaining = target - current
    if target <= 0:
        return _forecast_result("no_forecast")
    if remaining <= 0:
        return _forecast_result("achieved")
    monthly = _monthly_deposit_rate(snapshots)
    if monthly is None:
        return _forecast_result("no_forecast")
    if monthly <= 0:
        return _forecast_result("not_progressing", monthly_rate=monthly)
    months_remaining = remaining / monthly
    days = int((months_remaining * FORECAST_PERIOD_DAYS).to_integral_value(ROUND_CEILING))
    projected = today + timedelta(days=days)
    on_track = None if target_date is None else projected <= target_date
    return _forecast_result(
        _ON_TRACK_STATE[on_track], monthly_rate=monthly, projected_date=projected,
        months_remaining=months_remaining, on_track=on_track,
    )


def _goal_snapshots(db: Session, goal: SavingsGoal) -> list[tuple[date, Decimal]]:
    """Dated balance history backing a goal's deposit rate. Only linked goals have
    one; a manual goal tracks a single ``current_amount`` (no history → no rate)."""
    if goal.account_id is None:
        return []
    return [(b.as_of_date, Decimal(b.balance)) for b in balance_history(db, goal.account_id)]


def goal_to_dict(db: Session, goal: SavingsGoal) -> dict:
    current = goal_current(db, goal)
    target = Decimal(goal.target_amount)
    remaining = target - current
    percent = float(min(Decimal("100"), (current / target * 100))) if target > 0 else 0.0
    return {
        "id": goal.id,
        "name": goal.name,
        "target_amount": str(target),
        "target_date": goal.target_date.isoformat() if goal.target_date else None,
        "account_id": goal.account_id,
        "current_amount": str(goal.current_amount),
        "current": str(current.quantize(TWO_DP)),
        "remaining": str(remaining.quantize(TWO_DP)),
        "percent": round(percent, 1),
        "currency": goal.currency,
        "status": "achieved" if current >= target and target > 0 else goal.status,
        "forecast": forecast_goal(
            current=current, target=target, snapshots=_goal_snapshots(db, goal),
            target_date=goal.target_date, today=date.today(),
        ),
    }


def create_goal(db: Session, *, name: str, target_amount: Decimal,
                target_date: date | None = None, account_id: int | None = None,
                current_amount: Decimal | None = None, currency: str | None = None) -> SavingsGoal:
    if account_id is not None:
        get_savings_account(db, account_id)  # validate
    goal = SavingsGoal(
        household_id=get_or_create_default_household(db).id,
        name=name.strip(),
        target_amount=Decimal(target_amount).quantize(TWO_DP),
        target_date=target_date,
        account_id=account_id,
        current_amount=Decimal(current_amount or 0).quantize(TWO_DP),
        currency=(currency or settings_service.get_base_currency(db)).upper(),
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


def update_goal(db: Session, goal: SavingsGoal, **fields) -> SavingsGoal:
    if "status" in fields and fields["status"] is not None and fields["status"] not in GOAL_STATUSES:
        raise ValueError(f"status must be one of {sorted(GOAL_STATUSES)}")
    if fields.get("account_id") is not None:
        get_savings_account(db, fields["account_id"])
    for key, value in fields.items():
        # The route passes only client-supplied fields (exclude_unset), so an
        # explicit ``None`` here means "clear it". Honour that for the nullable
        # fields; ignore ``None`` on required fields (can't null them out).
        if value is not None or key in CLEARABLE_GOAL_FIELDS:
            setattr(goal, key, value)
    db.commit()
    db.refresh(goal)
    return goal


def delete_goal(db: Session, goal: SavingsGoal) -> None:
    db.delete(goal)
    db.commit()


def list_goals(db: Session) -> list[SavingsGoal]:
    return list(db.scalars(select(SavingsGoal).order_by(SavingsGoal.name)).all())


def summary(db: Session, *, account_ids: set[int] | None = None) -> dict:
    accounts = [account_to_dict(db, a) for a in list_accounts(db, account_ids=account_ids)]
    visible_ids = {a["id"] for a in accounts}
    # Drop goals linked to a now-hidden private account (its balance would leak via
    # goal_current); manual/unlinked goals stay visible to everyone.
    goals = [
        goal_to_dict(db, g)
        for g in list_goals(db)
        if g.account_id is None or g.account_id in visible_ids
    ]
    return {
        "currency": settings_service.get_base_currency(db),
        "total_savings": str(total_savings(db, account_ids=account_ids)),
        "accounts": accounts,
        "goals": goals,
    }
