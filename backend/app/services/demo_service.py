"""Demo data loader (backlog #16, enriched per "add more data to demo database").

Loads a fabricated dataset so a new user can see the app populated without
uploading real statements. The data is **generated relative to today** so it
always covers the current month plus the previous two (Trends + the current-
month dashboard view both look populated whenever the demo is loaded), and it is
deliberately varied so every page has something to show:

- many vendors across all the default categories (groceries, fuel, bills,
  eating out, subscriptions, transport, shopping, health, pets, entertainment);
- two **foreign-currency trips** (a Eurozone trip and a US trip) so the Travel
  page detects trips and shows spend-by-currency — FX rates are seeded so the
  rows convert to the base currency;
- a handful of **business** transactions with **VAT** so the Business page shows
  reclaimable VAT by category and month.

It runs through the real import pipeline (parse -> dedupe -> auto-categorise) and
then enriches the imported rows (FX + business/VAT), so re-running within the
same day is idempotent thanks to source-hash dedup. Generated in-code (no
packaged CSV files) so it works inside the add-on image too.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.session import dml_rowcount
from app.models import (
    Account,
    Asset,
    Budget,
    Category,
    ChildAllocation,
    HoldingPrice,
    Project,
    Receipt,
    ReviewItem,
    Rule,
    SavingsGoal,
    Statement,
    Subscription,
    Transaction,
    User,
    Vendor,
    VendorAlias,
)
from app.services import (
    allowance_service,
    asset_service,
    fx_service,
    import_service,
    investment_service,
    receipt_service,
    review_service,
    savings_service,
    settings_service,
    split_service,
    vendor_service,
)
from app.services.household_service import get_or_create_default_household

# Representative GBP-per-1-unit rates for the demo's foreign spend. Manual rates
# are seeded for each foreign row's date so the rows convert to base (otherwise a
# manual-mode import leaves them needs_rate=True / out of every total).
_DEMO_FX_RATES = {"EUR": Decimal("0.86"), "USD": Decimal("0.79")}

# Number of monthly cycles to generate (current month + previous two).
_CYCLES = 3
# Days between cycles (≈ a month; keeps rows in distinct months without calendar
# arithmetic per row — see _spec_date).
_CYCLE_DAYS = 30

# Merchant descriptions reused across the demo tables and the enrichment helpers
# below; kept as constants so the seeded strings stay in exactly one place.
_MERCHANT_ODEON = "ODEON CINEMA"
_MERCHANT_GOUSTO = "GOUSTO MEAL KIT"
_MERCHANT_BOILER = "BOILER CARE PLAN"
_MERCHANT_AMAZON_OFFICE = "AMAZON OFFICE SUPPLIES"


@dataclass(frozen=True)
class _Spec:
    """A demo transaction before it becomes a CSV row + optional enrichment."""

    cycle: int  # 0 is this month, 1 is last month, 2 is two months ago
    day: int  # day offset within the cycle (0..28)
    description: str
    amount: Decimal  # signed amount (negative means money spent)
    currency: str = "GBP"
    vary: bool = False  # nudge spend a little per cycle so trends aren't flat
    business: bool = False
    vat: Decimal | None = None  # in the transaction's own currency


# --- Recurring monthly template (applied to every cycle) ---------------------
# (day, description, amount, vary) — amount is the spend magnitude (made negative
# below); income rows use a positive amount and vary=False.
_MONTHLY_SPEND: list[tuple[int, str, str, bool]] = [
    (1, "NATIONWIDE MORTGAGE", "875.00", False),
    (1, "DARTFORD BOROUGH COUNCIL TAX", "158.00", False),
    (3, "BRITISH GAS ENERGY", "96.50", True),
    (3, "THAMES WATER", "38.00", False),
    (4, "BT BROADBAND", "33.99", False),
    (4, "VODAFONE MOBILE", "22.00", False),
    (5, "ADMIRAL CAR INSURANCE", "47.30", False),
    (0, "TESCO STORES 3142", "58.40", True),
    (8, "SAINSBURYS SUPERMARKET", "49.20", True),
    (14, "ALDI STORE 412", "37.85", True),
    (21, "LIDL GB DARTFORD", "41.10", True),
    (26, "WAITROSE AND PARTNERS", "52.70", True),
    (6, "SHELL DARTFORD", "64.30", True),
    (20, "BP CONNECT", "59.80", True),
    (7, "NETFLIX.COM", "10.99", False),
    (9, "SPOTIFY UK", "11.99", False),
    (10, "DISNEY PLUS", "7.99", False),
    (11, "PURE GYM", "24.99", False),
    (1, "COSTA COFFEE 482", "4.15", True),
    (13, "PRET A MANGER", "8.60", True),
    (16, "DELIVEROO", "27.40", True),
    (23, "NANDOS DARTFORD", "31.20", True),
    (15, "TfL TRAVEL CHARGE", "12.80", True),
    (18, "TRAINLINE", "18.50", True),
    (17, "AMAZON MARKETPLACE", "23.49", True),
    (24, "ARGOS RETAIL", "34.99", True),
    (19, "BOOTS PHARMACY", "14.49", False),
    (22, "PETS AT HOME", "28.00", False),
    (25, _MERCHANT_ODEON, "21.00", False),
]
# (day, description, amount) — money in.
_MONTHLY_INCOME: list[tuple[int, str, str]] = [
    (0, "SALARY ACME LTD", "2450.00"),
    (28, "BARCLAYS INTEREST", "3.20"),
]

# --- Extra recurring merchants at non-monthly cadences -----------------------
# The monthly template above only ever produces *monthly* subscriptions. These
# distinct merchants are charged a flat amount at a regular non-monthly gap so
# subscription detection (which runs during the import) also surfaces
# **fortnightly** and **bi-monthly** cycles. (days_ago, description, amount) — the
# gap between rows is what the detector classifies, so the offsets matter.
_RECURRING_EXTRA: list[tuple[int, str, str]] = [
    # Fortnightly meal-kit box (~14-day gaps → "fortnightly").
    (2, _MERCHANT_GOUSTO, "34.95"),
    (16, _MERCHANT_GOUSTO, "34.95"),
    (30, _MERCHANT_GOUSTO, "34.95"),
    (44, _MERCHANT_GOUSTO, "34.95"),
    # Bi-monthly boiler care plan (~61-day gaps → "bi_monthly").
    (4, _MERCHANT_BOILER, "18.00"),
    (65, _MERCHANT_BOILER, "18.00"),
    (126, _MERCHANT_BOILER, "18.00"),
]

# --- One-off business expenses (GBP) with VAT, by cycle ----------------------
# (cycle, day, description, amount, vat)
_BUSINESS: list[tuple[int, int, str, str, str]] = [
    (0, 16, "TRAINLINE RAIL TICKET", "84.00", "14.00"),
    (0, 20, _MERCHANT_AMAZON_OFFICE, "54.00", "9.00"),
    (0, 24, "SCREWFIX DIRECT", "120.00", "20.00"),
    (1, 6, "PREMIER INN HOTEL", "96.00", "16.00"),
    (1, 22, "COSTA COFFEE CLIENT", "18.00", "3.00"),
]

# --- Foreign-currency trips (clustered days within one cycle) ----------------
# (cycle, day, description, amount, currency, business, vat)
_TRIPS: list[tuple[int, int, str, str, str, bool, str | None]] = [
    # Eurozone trip (last month) — the hotel is a business expense with EUR VAT.
    (1, 9, "IBERIA AIRLINES MADRID", "210.00", "EUR", False, None),
    (1, 10, "HOTEL BARCELONA CENTRE", "168.00", "EUR", True, "28.00"),
    (1, 11, "RENFE RAIL", "24.50", "EUR", False, None),
    (1, 11, "CAFE DE BARCELONA", "18.20", "EUR", False, None),
    (1, 12, "EL CORTE INGLES", "64.00", "EUR", False, None),
    (1, 13, "TAPAS BCN", "42.00", "EUR", False, None),
    (1, 14, "MUSEU PICASSO", "14.00", "EUR", False, None),
    # United States trip (two months ago).
    (2, 9, "BRITISH AIRWAYS", "340.00", "USD", False, None),
    (2, 10, "MARRIOTT HOTEL NYC", "295.00", "USD", False, None),
    (2, 11, "YELLOW CAB NYC", "28.00", "USD", False, None),
    (2, 12, "STARBUCKS NEW YORK", "9.40", "USD", False, None),
    (2, 13, "MACYS NYC", "120.00", "USD", False, None),
    (2, 14, "MOMA MUSEUM", "25.00", "USD", False, None),
]


def _vary(amount: Decimal, cycle: int) -> Decimal:
    """Older months spend a little more, so Trends slope (recent = greener)."""
    return (amount + Decimal(cycle) * Decimal("3.00")).quantize(Decimal("0.01"))


def _build_specs() -> list[_Spec]:
    specs: list[_Spec] = []
    for cycle in range(_CYCLES):
        for day, desc, amt, vary in _MONTHLY_SPEND:
            base = Decimal(amt)
            value = _vary(base, cycle) if vary else base
            specs.append(_Spec(cycle, day, desc, -value, vary=vary))
        for day, desc, amt in _MONTHLY_INCOME:
            specs.append(_Spec(cycle, day, desc, Decimal(amt)))
    # Non-monthly recurring rows live in cycle 0 with the day offset carrying the
    # whole "days ago" (``_spec_date`` = cycle*30 + day, so cycle 0 keeps it exact).
    for days_ago, desc, amt in _RECURRING_EXTRA:
        specs.append(_Spec(0, days_ago, desc, -Decimal(amt)))
    for cycle, day, desc, amt, vat in _BUSINESS:
        specs.append(_Spec(cycle, day, desc, -Decimal(amt), business=True, vat=Decimal(vat)))
    for cycle, day, desc, amt, cur, business, vat in _TRIPS:
        specs.append(
            _Spec(
                cycle,
                day,
                desc,
                -Decimal(amt),
                currency=cur,
                business=business,
                vat=Decimal(vat) if vat is not None else None,
            )
        )
    return specs


def _spec_date(today: date, spec: _Spec) -> date:
    """Map a (cycle, day) spec to a real date no later than today."""
    days_ago = spec.cycle * _CYCLE_DAYS + spec.day
    return today - timedelta(days=days_ago)


def _build_csv(today: date, specs: list[_Spec]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Description", "Amount", "Currency", "Card"])
    for spec in specs:
        writer.writerow(
            [
                _spec_date(today, spec).isoformat(),
                spec.description,
                f"{spec.amount:.2f}",
                spec.currency,
                "Visa",
            ]
        )
    return buf.getvalue()


def _enrich(db: Session, statement_id: int, specs: list[_Spec]) -> None:
    """Seed FX rates for foreign rows (so they convert) and flag business/VAT.

    Scoped to the rows just imported (this statement). Idempotent: setting the
    same rate/flags again is a no-op, and a re-run imports nothing new.
    """
    base = settings_service.get_base_currency(db)
    fx_mode = settings_service.get_fx_mode(db)
    business_vat = {
        s.description: s.vat for s in specs if s.business and s.vat is not None
    }
    business_descriptions = {s.description for s in specs if s.business}

    rows = list(
        db.scalars(
            select(Transaction).where(Transaction.statement_id == statement_id)
        ).all()
    )

    # 1. Seed a manual rate per (date, currency) for foreign spend, then convert.
    seeded = False
    for row in rows:
        rate = _DEMO_FX_RATES.get(row.currency)
        if rate is not None and row.currency != base:
            fx_service.set_manual_rate(
                db, on=row.transaction_date, base=base, quote=row.currency, rate=rate
            )
            seeded = True
    if seeded:
        fx_service.backfill_missing(db, base, fx_mode)

    # 2. Flag business expenses + their VAT (in the row's own currency).
    for row in rows:
        if row.description_raw in business_descriptions:
            row.is_business = True
            row.vat_amount = business_vat.get(row.description_raw)
    db.commit()


# --- Example data so every page has something to show ------------------------
# One example of each feature, seeded after the transactions land. Each piece is
# guarded by a recognizable name/id so a re-run within the same day is a no-op.
_DEMO_RULE_NAME = "Demo: Deliveroo → Eating Out"
# (external_id, display_name, role)
_DEMO_MEMBER = ("demo-member", "Sam (partner)", "member")
_DEMO_CHILD = ("demo-child", "Alex (age 12)", "child")

# The partner member's OWN monthly statement, imported onto their own account —
# the normal household flow (each member imports their own statement), and what
# gives the per-member filter + Mine/Shared/All toggle real data. Distinct
# merchants so it reads as Sam's own spend. (day, description, amount)
_MEMBER_SPEND: list[tuple[int, str, str]] = [
    (2, "EE MOBILE", "21.00"),
    (6, "ANYTIME FITNESS", "32.99"),
    (10, "SPOTIFY DUO", "16.99"),
    (14, "UBER TRIP", "13.40"),
    (18, "ASOS.COM", "44.50"),
    (22, "GAIL'S BAKERY", "7.80"),
    (27, "GREGGS DARTFORD", "6.40"),
]


def _cat_id(db: Session, name: str) -> int | None:
    return db.scalar(select(Category.id).where(Category.name == name))


# Category names reused across the demo seeds (vendors, rules, budgets).
_CAT_GROCERIES = "Groceries"
_CAT_SUBSCRIPTIONS = "Subscriptions"
_CAT_EATING_OUT = "Eating Out"
_CAT_BILLS = "Bills"

# A vendor library (canonical name, "contains" alias, default category) so the
# Vendors page is populated and the demo transactions link to real vendor rows
# (merchant_id) rather than only carrying raw merchant text.
_DEMO_VENDORS: list[tuple[str, str, str]] = [
    ("Tesco", "TESCO", _CAT_GROCERIES),
    ("Sainsbury's", "SAINSBURYS", _CAT_GROCERIES),
    ("Aldi", "ALDI", _CAT_GROCERIES),
    ("Lidl", "LIDL", _CAT_GROCERIES),
    ("Waitrose", "WAITROSE", _CAT_GROCERIES),
    ("Shell", "SHELL", "Car"),
    ("BP", "BP CONNECT", "Car"),
    ("Netflix", "NETFLIX", _CAT_SUBSCRIPTIONS),
    ("Spotify", "SPOTIFY", _CAT_SUBSCRIPTIONS),
    ("Disney+", "DISNEY PLUS", _CAT_SUBSCRIPTIONS),
    ("PureGym", "PURE GYM", _CAT_SUBSCRIPTIONS),
    ("Costa Coffee", "COSTA COFFEE", _CAT_EATING_OUT),
    ("Pret A Manger", "PRET A MANGER", _CAT_EATING_OUT),
    ("Deliveroo", "DELIVEROO", _CAT_EATING_OUT),
    ("Nando's", "NANDOS", _CAT_EATING_OUT),
    ("Transport for London", "TfL", "Transport"),
    ("Trainline", "TRAINLINE", "Transport"),
    ("Amazon", "AMAZON", "Shopping"),
    ("Argos", "ARGOS", "Shopping"),
    ("Boots", "BOOTS", "Health"),
    ("Pets at Home", "PETS AT HOME", "Pets"),
    ("Odeon", "ODEON", "Entertainment"),
    ("British Gas", "BRITISH GAS", _CAT_BILLS),
    ("Thames Water", "THAMES WATER", _CAT_BILLS),
    ("BT", "BT BROADBAND", _CAT_BILLS),
    ("Vodafone", "VODAFONE", _CAT_BILLS),
    ("Admiral", "ADMIRAL", "Insurance"),
    ("Nationwide", "NATIONWIDE MORTGAGE", "Housing"),
    ("Screwfix", "SCREWFIX", "DIY"),
]


def _seed_vendors(db: Session, rows: list[Transaction]) -> None:
    """Seed a vendor library + link the imported demo transactions to it.

    Import-time auto-categorisation matched these merchants by keyword (no Vendor
    rows existed yet), so the Vendors page was empty. Create the vendors now, then
    re-run normalisation on the demo rows so each gets a ``merchant_id`` (vendor
    stats, the dashboard's top-vendors and the vendor filter then all work)."""
    household = get_or_create_default_household(db)
    created_any = False
    for canonical, alias, category in _DEMO_VENDORS:
        if db.scalar(select(Vendor.id).where(Vendor.canonical_name == canonical)):
            continue
        vendor = Vendor(
            household_id=household.id,
            canonical_name=canonical,
            display_name=canonical,
            default_category_id=_cat_id(db, category),
            created_by="import",
        )
        db.add(vendor)
        db.flush()
        db.add(VendorAlias(vendor_id=vendor.id, alias=alias, match_type="contains", source="import"))
        created_any = True
    if created_any:
        db.flush()
        # Link the just-imported transactions (won't overwrite an existing
        # category — only fills merchant_id and any still-blank category).
        for txn in rows:
            vendor_service.normalise_transaction(db, txn)


# Obvious duplicate / alias-y vendor pairs so the vendor-merge UI has real
# candidates to consolidate. Each near-duplicate vendor is created and a subset of
# already-linked demo transactions is re-pointed onto it, so both the original and
# the duplicate carry spend (a merge that actually consolidates something).
# (duplicate canonical name, original canonical name, descriptions to move onto it)
_DEMO_MERGE_VENDORS: list[tuple[str, str, set[str]]] = [
    ("Amazon UK", "Amazon", {_MERCHANT_AMAZON_OFFICE}),
    ("Costa", "Costa Coffee", {"COSTA COFFEE CLIENT"}),
]


def _seed_merge_candidate_vendors(db: Session, rows: list[Transaction]) -> None:
    """Seed a couple of near-duplicate vendors (each with some re-pointed demo
    spend) so the vendor-merge feature has obvious candidates to act on. Idempotent:
    guarded by the duplicate's canonical name; the re-point only touches demo rows."""
    household = get_or_create_default_household(db)
    for dup_name, original_name, descriptions in _DEMO_MERGE_VENDORS:
        if db.scalar(select(Vendor.id).where(Vendor.canonical_name == dup_name)):
            continue
        original = db.scalar(select(Vendor).where(Vendor.canonical_name == original_name))
        dup = Vendor(
            household_id=household.id,
            canonical_name=dup_name,
            display_name=dup_name,
            default_category_id=original.default_category_id if original else None,
            created_by="import",
        )
        db.add(dup)
        db.flush()
        db.add(VendorAlias(vendor_id=dup.id, alias=dup_name.upper(), match_type="contains", source="import"))
        for txn in rows:
            if txn.description_raw in descriptions:
                txn.merchant_id = dup.id
    db.flush()


def _seed_rule(db: Session) -> None:
    """One categorisation rule so the Rules page isn't empty."""
    if db.scalar(select(Rule.id).where(Rule.name == _DEMO_RULE_NAME)):
        return
    eating = _cat_id(db, _CAT_EATING_OUT)
    db.add(
        Rule(
            household_id=get_or_create_default_household(db).id,
            name=_DEMO_RULE_NAME,
            priority=200,
            condition_type="description_contains",
            condition_value="DELIVEROO",
            action_type="set_category",
            action_value=str(eating) if eating else None,
            created_from="user",
        )
    )


def _assign_project(db: Session, name: str, txns: list[Transaction], *, budget: Decimal) -> None:
    if not txns or db.scalar(select(Project.id).where(Project.name == name)):
        return
    dates = [t.transaction_date for t in txns]
    project = Project(
        household_id=get_or_create_default_household(db).id,
        name=name,
        status="active",
        budget_amount=budget,
        start_date=min(dates),
        end_date=max(dates),
    )
    db.add(project)
    db.flush()
    for t in txns:
        t.project_id = project.id


def _seed_projects(db: Session, rows: list[Transaction]) -> None:
    """Two projects with transactions assigned (one drawn from the Spain trip)."""
    eur = [t for t in rows if t.currency == "EUR"]
    _assign_project(db, "Spain City Break", eur, budget=Decimal("1200.00"))
    office_desc = {_MERCHANT_AMAZON_OFFICE, "SCREWFIX DIRECT", "ARGOS RETAIL"}
    office = [t for t in rows if t.description_raw in office_desc]
    _assign_project(db, "Home Office Setup", office, budget=Decimal("500.00"))


def _seed_budget(db: Session, name: str, *, amount: Decimal, category: str | None) -> None:
    if db.scalar(select(Budget.id).where(Budget.name == name, Budget.owner_user_id.is_(None))):
        return
    db.add(
        Budget(
            household_id=get_or_create_default_household(db).id,
            name=name,
            category_id=_cat_id(db, category) if category else None,
            period="monthly",
            amount=amount,
            alert_threshold_percent=80,
        )
    )


def _seed_budgets(db: Session) -> None:
    """A couple of category budgets + a total, so the Budgets page shows progress."""
    _seed_budget(db, _CAT_GROCERIES, amount=Decimal("450.00"), category=_CAT_GROCERIES)
    _seed_budget(db, _CAT_EATING_OUT, amount=Decimal("150.00"), category=_CAT_EATING_OUT)
    _seed_budget(db, "Monthly spending", amount=Decimal("2000.00"), category=None)


def _seed_savings(db: Session) -> None:
    """A savings account with a growing balance history + two goals."""
    if db.scalar(
        select(Account.id).where(
            Account.name == "Emergency Fund", Account.account_type == savings_service.SAVINGS_TYPE
        )
    ):
        return
    account = savings_service.create_account(db, name="Emergency Fund", institution="Demo Building Society")
    today = date.today()
    for days_ago, bal in ((90, "4000"), (60, "4600"), (30, "5300"), (0, "5900")):
        savings_service.record_balance(
            db, account.id, as_of=today - timedelta(days=days_ago), balance=Decimal(bal)
        )
    savings_service.create_goal(
        db, name="6 months' expenses", target_amount=Decimal("10000.00"), account_id=account.id
    )
    savings_service.create_goal(
        db, name="New car fund", target_amount=Decimal("8000.00"), current_amount=Decimal("3200.00")
    )


def _seed_household(db: Session, rows: list[Transaction]) -> None:
    """A second member + a child with a pocket-money budget and a few allowance
    allocations (a non-destructive overlay on the parent's own spend)."""
    household = get_or_create_default_household(db)
    for ext, name, role in (_DEMO_MEMBER, _DEMO_CHILD):
        if db.scalar(select(User.id).where(User.external_id == ext)):
            continue
        db.add(
            User(
                household_id=household.id,
                external_id=ext,
                display_name=name,
                role=role,
                status="approved",
                is_active=True,
            )
        )
    db.flush()

    child = db.scalar(select(User).where(User.external_id == _DEMO_CHILD[0]))
    if child is None:
        return
    if not db.scalar(select(Budget.id).where(Budget.owner_user_id == child.id)):
        db.add(
            Budget(
                household_id=household.id,
                owner_user_id=child.id,
                name="Pocket money",
                period="monthly",
                amount=Decimal("20.00"),
                alert_threshold_percent=80,
            )
        )
        db.flush()
    if not allowance_service.list_allocations(db, child.id):
        treats = {"COSTA COFFEE 482", _MERCHANT_ODEON, "NANDOS DARTFORD"}
        for t in [r for r in rows if r.description_raw in treats][:4]:
            allowance_service.create_allocation(db, child_id=child.id, transaction_id=t.id)


def _claim_main_account(db: Session, rows: list[Transaction]) -> None:
    """Give the main imported account an owner (the household owner), so the
    per-member filter + Mine/Shared/All toggle attribute its spend to that person.
    Without an owner every account is household-shared and those views are empty.
    Idempotent: a re-run imports no rows, so this is a no-op."""
    if not rows:
        return
    owner = db.scalar(
        select(User).where(User.role == "owner", User.status == "approved").order_by(User.id)
    )
    main = db.get(Account, rows[0].account_id)
    if owner is not None and main is not None and main.owner_user_id is None:
        main.owner_user_id = owner.id


def _build_member_csv(today: date) -> str:
    """The partner member's own statement (their monthly spend across the cycles)."""
    specs = [
        _Spec(cycle, day, desc, -Decimal(amt))
        for cycle in range(_CYCLES)
        for day, desc, amt in _MEMBER_SPEND
    ]
    return _build_csv(today, specs)


def _import_member_statement(db: Session, today: date) -> dict | None:
    """Import the partner member's OWN statement onto their own account — the
    normal household flow (each member imports their own), and what gives the
    per-member filter real data. Returns the import report (or None if the member
    isn't present). Idempotent: a re-run dedups to zero new (scoped to the
    member's account)."""
    sam = db.scalar(select(User).where(User.external_id == _DEMO_MEMBER[0]))
    if sam is None:
        return None
    account = db.scalar(select(Account).where(Account.owner_user_id == sam.id))
    if account is None:
        account = Account(
            household_id=get_or_create_default_household(db).id,
            name="Sam's Card",
            institution="Monzo",
            account_type="current_account",
            currency=settings_service.get_base_currency(db),
            owner_user_id=sam.id,
            is_shared=False,
        )
        db.add(account)
        db.flush()
    result = import_service.create_import(
        db,
        filename="demo-sam.csv",
        content=_build_member_csv(today).encode("utf-8"),
        parser_id="curve_csv",
        account_id=account.id,
    )
    confirmed = import_service.confirm_import(db, result["import_id"])
    return confirmed["report"]


def _seed_review_queue(db: Session, rows: list[Transaction]) -> None:
    """Flag a few uncategorised foreign purchases for review so the Review Queue
    (and the Needs-Review filter) aren't empty."""
    uncategorised = [t for t in rows if t.category_id is None and not t.is_transfer][:4]
    for t in uncategorised:
        t.needs_review = True
        t.review_reason = "unknown_category"
        review_service.add(
            db,
            item_type="transaction",
            item_id=t.id,
            reason="unknown_category",
            severity="warning",
            suggested_action="Assign a category to this foreign purchase.",
        )


_DEMO_CAR_NAME = "Family Car"
_DEMO_HOME_NAME = "Home"
_DEMO_ISA_NAME = "Stocks & Shares ISA"
_DEMO_PENSION_NAME = "Workplace Pension"


def _seed_assets(db: Session) -> None:
    """A car with three full fills (→ tank-to-tank MPG) plus a service, and a home
    with electricity + gas meter readings — so the Cars & Assets page is populated."""
    if db.scalar(select(Asset.id).where(Asset.name == _DEMO_CAR_NAME)):
        return
    today = date.today()
    car = asset_service.create_asset(
        db, name=_DEMO_CAR_NAME, kind="car", identifier="AB12 CDE", distance_unit="mi"
    )
    # Two tank-to-tank segments (~41 MPG): each leg is 360 mi on a full tank.
    for days_ago, odo, litres, cost in (
        (62, "41200", "45.0", "66.00"),
        (34, "41560", "40.0", "59.00"),
        (7, "41920", "39.5", "58.50"),
    ):
        asset_service.add_log(
            db, car.id, log_date=today - timedelta(days=days_ago), kind="refuel",
            odometer=odo, litres=litres, cost=cost, is_full_tank=True, fuel_type="petrol",
        )
    asset_service.add_log(
        db, car.id, log_date=today - timedelta(days=20), kind="service",
        cost="180.00", note="Annual service + MOT",
    )
    home = asset_service.create_asset(db, name=_DEMO_HOME_NAME, kind="home", identifier="12 Demo Street")
    for meter, unit, r0, r1, cost in (
        ("electricity", "kWh", "48210", "48560", "98.00"),
        ("gas", "m3", "12880", "13110", "71.00"),
    ):
        asset_service.add_log(db, home.id, log_date=today - timedelta(days=62), kind="reading",
                              meter=meter, reading=r0, unit=unit)
        asset_service.add_log(db, home.id, log_date=today - timedelta(days=32), kind="reading",
                              meter=meter, reading=r1, unit=unit, cost=cost)


def _seed_price_history(db: Session, holding_id: int, points: list[tuple[int, str]]) -> None:
    """Back-fill historical price points for a holding (one row per date) so the
    portfolio-value chart renders a line rather than a single dot. Today's price is
    already recorded by ``create_holding``; skip it and any date already present."""
    today = date.today()
    for days_ago, price in points:
        if days_ago == 0:
            continue
        on = today - timedelta(days=days_ago)
        if db.scalar(
            select(HoldingPrice.id).where(
                HoldingPrice.holding_id == holding_id, HoldingPrice.as_of_date == on
            )
        ):
            continue
        db.add(HoldingPrice(holding_id=holding_id, as_of_date=on, price=Decimal(price)))
    db.flush()


def _seed_investments(db: Session) -> None:
    """An investment account with two holdings (→ market value + unrealised gain,
    plus back-filled price history so the value chart renders) and a workplace
    pension with a growing value series — so the Investments page is populated
    (both tracking modes)."""
    if db.scalar(
        select(Account.id).where(Account.name == _DEMO_ISA_NAME, Account.account_type == "investment")
    ):
        return
    today = date.today()
    isa = investment_service.create_account(
        db, name=_DEMO_ISA_NAME, institution="Demo Invest", account_type="investment"
    )
    vwrl = investment_service.create_holding(
        db, isa.id, symbol="VWRL", name="Vanguard FTSE All-World",
        units="120", avg_cost="92.50", last_price="108.20",
    )
    aapl = investment_service.create_holding(
        db, isa.id, symbol="AAPL", name="Apple Inc.",
        units="15", avg_cost="145.00", last_price="171.30",
    )
    # A rising price series per holding (ending at today's last_price) → the ISA's
    # value chart shows a trend.
    _seed_price_history(db, vwrl.id, [(90, "99.00"), (60, "102.50"), (30, "105.80")])
    _seed_price_history(db, aapl.id, [(90, "150.00"), (60, "158.00"), (30, "165.00")])
    pension = investment_service.create_account(
        db, name=_DEMO_PENSION_NAME, institution="Demo Pensions", account_type="pension"
    )
    investment_service.record_value(db, pension.id, as_of=today - timedelta(days=90), value=Decimal("38400.00"))
    investment_service.record_value(db, pension.id, as_of=today - timedelta(days=45), value=Decimal("39900.00"))
    investment_service.record_value(db, pension.id, as_of=today, value=Decimal("41250.00"))


_CAT_ENTERTAINMENT = "Entertainment"
_DEMO_RECEIPT_FILENAME = "waitrose-receipt.txt"
_DEMO_RECEIPT_BYTES = (
    b"WAITROSE & PARTNERS\nDartford\n\nGroceries.......52.70\nTOTAL  GBP 52.70\n"
    b"Thank you for shopping with us\n"
)


def _seed_split(db: Session, rows: list[Transaction]) -> None:
    """Split one transaction across two categories so the split UI + the split-aware
    category breakdown have an example. Idempotent: skips an already-split row."""
    odeon = next((t for t in rows if t.description_raw == _MERCHANT_ODEON), None)
    if odeon is None or odeon.is_split:
        return
    entertainment = _cat_id(db, _CAT_ENTERTAINMENT)
    eating = _cat_id(db, _CAT_EATING_OUT)
    if entertainment is None or eating is None:
        return
    halves = split_service.split_evenly(odeon.amount, 2)
    split_service.set_splits(
        db,
        odeon,
        [
            split_service.SplitInput(amount=halves[0], category_id=entertainment, description="Tickets"),
            split_service.SplitInput(amount=halves[1], category_id=eating, description="Snacks & drinks"),
        ],
    )


def _seed_receipt(db: Session, rows: list[Transaction]) -> None:
    """Attach a processed receipt to a grocery transaction so the Receipts page and
    the transaction's receipt link are populated. Idempotent: skips if the row
    already has a receipt (and ``store_upload`` dedups by content hash anyway)."""
    waitrose = next((t for t in rows if t.description_raw == "WAITROSE AND PARTNERS"), None)
    if waitrose is None or receipt_service.receipts_for_transaction(db, waitrose.id):
        return
    receipt, _created = receipt_service.store_upload(db, _DEMO_RECEIPT_FILENAME, _DEMO_RECEIPT_BYTES)
    receipt_service.set_fields(
        db,
        receipt,
        merchant_raw="Waitrose & Partners",
        receipt_date=waitrose.transaction_date,
        total_amount=abs(waitrose.amount),
        currency="GBP",
    )
    receipt_service.attach_to_transaction(db, receipt, waitrose.id)


def _seed_examples(db: Session, statement_id: int) -> None:
    """Seed one example of each feature so a fresh demo shows off every page.
    Idempotent — each piece is guarded by a recognizable name/id, and a re-run
    imports no new transactions so ``rows`` is empty."""
    rows = list(
        db.scalars(select(Transaction).where(Transaction.statement_id == statement_id)).all()
    )
    _seed_vendors(db, rows)
    _seed_merge_candidate_vendors(db, rows)
    _seed_rule(db)
    _seed_projects(db, rows)
    _seed_budgets(db)
    _seed_savings(db)
    _seed_assets(db)
    _seed_investments(db)
    _seed_household(db, rows)
    _claim_main_account(db, rows)
    _seed_review_queue(db, rows)
    _seed_split(db, rows)
    _seed_receipt(db, rows)
    db.commit()


# --- Demo manifest (so "Remove demo data" deletes exactly what a load made) ---
# Tables whose *new* rows are captured by a before/after id diff during a load.
# Transactions aren't listed — they're derived from the demo statements at removal
# time. Savings accounts are regular Accounts, so they ride the "accounts" key.
_MANIFEST_MODELS: dict[str, type] = {
    "statements": Statement,
    "accounts": Account,
    # Demo cars/home; their refuel/reading logs cascade (FK ON DELETE) on removal.
    "assets": Asset,
    "vendors": Vendor,
    "rules": Rule,
    "projects": Project,
    "budgets": Budget,
    "savings_goals": SavingsGoal,
    # Subscriptions are auto-detected from the demo's recurring transactions during
    # the import, so capture them too — otherwise they'd survive a remove (bug fix).
    "subscriptions": Subscription,
    # A receipt attached to a demo transaction. Its match cascades when the txn goes,
    # but the Receipt row itself is independent, so capture it (and drop its stored
    # file on removal) — otherwise it (and its file) would survive a remove.
    "receipts": Receipt,
    "users": User,
}


def _snapshot_ids(db: Session) -> dict[str, set[int]]:
    """The current set of ids in each manifest table (used to diff a load)."""
    return {
        name: set(db.scalars(select(model.id)).all())
        for name, model in _MANIFEST_MODELS.items()
    }


def _record_manifest(db: Session, before: dict[str, set[int]], after: dict[str, set[int]]) -> None:
    """Persist the rows this load created, merged with any prior manifest, so a
    later "Remove demo data" deletes only the demo's own rows (never a real import
    or anything the user added afterwards). Pre-existing rows are in ``before`` and
    so are never captured."""
    raw = settings_service.get(db, settings_service.DEMO_MANIFEST)
    manifest: dict[str, list[int]] = json.loads(raw) if raw else {}
    for name in _MANIFEST_MODELS:
        created = after[name] - before[name]
        manifest[name] = sorted(set(manifest.get(name, [])) | created)
    settings_service.set_value(db, settings_service.DEMO_MANIFEST, json.dumps(manifest))


def load_demo(db: Session) -> dict:
    """Import + enrich the demo dataset, then seed an example of each feature.
    Idempotent within a day (duplicates are skipped); returns the import report."""
    today = date.today()
    before = _snapshot_ids(db)
    specs = _build_specs()
    csv_text = _build_csv(today, specs)

    result = import_service.create_import(
        db,
        filename="demo-curve.csv",
        content=csv_text.encode("utf-8"),
        parser_id="curve_csv",
    )
    statement_id = result["import_id"]
    confirmed = import_service.confirm_import(db, statement_id)
    _enrich(db, statement_id, specs)
    _seed_examples(db, statement_id)

    # The partner member imports their own statement onto their own account (the
    # normal household flow) so the per-member filter has data. Combine its counts
    # into the report so "new == total transactions" stays true (idempotency).
    report = dict(confirmed["report"])
    member_report = _import_member_statement(db, today)
    if member_report:
        for key in ("rows_detected", "new", "duplicates", "errors"):
            report[key] = report.get(key, 0) + member_report.get(key, 0)

    # Record exactly what this load created so "Remove demo data" can undo it.
    _record_manifest(db, before, _snapshot_ids(db))

    # Demo defaults to DEBUG logging so there's something to see in the Logs/
    # add-on panel while exploring (reversible from Settings → Logging).
    from app.logging import set_level

    settings_service.set_value(db, settings_service.LOG_LEVEL, "DEBUG")
    set_level("DEBUG")
    return report


def has_demo_data(db: Session) -> bool:
    """True when a manifest from a previous load is present (something to remove)."""
    return bool(settings_service.get(db, settings_service.DEMO_MANIFEST))


def _bulk_delete(db: Session, model: type, ids: list[int]) -> int:
    """Delete rows of ``model`` by id (no-op for an empty list); returns the count."""
    if not ids:
        return 0
    stmt = delete(model).where(model.id.in_(ids)).execution_options(synchronize_session=False)
    return dml_rowcount(db.execute(stmt)) or 0


def _remove_demo_transactions(db: Session, manifest: dict[str, list[int]], counts: dict[str, int]) -> None:
    """Delete the demo statements and everything keyed off their transactions."""
    # Transactions on the demo statements (derived — not stored in the manifest).
    stmt_ids = manifest.get("statements", [])
    txn_ids = (
        list(db.scalars(select(Transaction.id).where(Transaction.statement_id.in_(stmt_ids))).all())
        if stmt_ids
        else []
    )
    if txn_ids:
        # Review items reference transactions by plain id (no FK) — drop them first.
        counts["review_items"] = dml_rowcount(db.execute(
            delete(ReviewItem)
            .where(ReviewItem.item_type == "transaction", ReviewItem.item_id.in_(txn_ids))
            .execution_options(synchronize_session=False)
        )) or 0
        # Allowance allocations drawn from these transactions (also cascade when the
        # demo child is deleted; cleared here so no non-demo child can dangle).
        db.execute(
            delete(ChildAllocation)
            .where(ChildAllocation.transaction_id.in_(txn_ids))
            .execution_options(synchronize_session=False)
        )
        # Transactions: splits / receipt-matches / tag links cascade (FK ON DELETE).
        counts["transactions"] = _bulk_delete(db, Transaction, txn_ids)
    counts["statements"] = _bulk_delete(db, Statement, stmt_ids)


def _remove_demo_receipts(db: Session, manifest: dict[str, list[int]], counts: dict[str, int]) -> None:
    """Delete the demo's receipts, dropping each stored original file first (the
    file isn't captured by the id-diff). Receipt-match rows cascade (FK ON DELETE)."""
    removed = 0
    for r_id in manifest.get("receipts", []):
        receipt = db.get(Receipt, r_id)
        if receipt is None:
            continue
        receipt_service.drop_original(db, receipt, commit=False)  # unlink the file
        db.delete(receipt)
        removed += 1
    counts["receipts"] = removed


def _delete_if_unreferenced(db: Session, model: type, ids: list[int], ref_column) -> int:
    """ORM-delete each row in ``ids`` whose ``ref_column`` no transaction still uses
    (so a shared account/vendor a real import touched is kept). Returns the count."""
    removed = 0
    for row_id in ids:
        row = db.get(model, row_id)
        if row is None:
            continue
        if db.scalar(select(func.count()).select_from(Transaction).where(ref_column == row_id)):
            continue
        db.delete(row)
        removed += 1
    return removed


def _reset_demo_settings(db: Session) -> None:
    """Clear the spent manifest and undo the demo's DEBUG logging default."""
    from app.logging import set_level

    settings_service.set_value(db, settings_service.DEMO_MANIFEST, "")
    default_level = settings_service._defaults()[settings_service.LOG_LEVEL]
    settings_service.set_value(db, settings_service.LOG_LEVEL, default_level)
    set_level(default_level)


def remove_demo(db: Session) -> dict:
    """Delete everything a previous :func:`load_demo` created, using the recorded
    manifest of row ids — so only demo rows go, never a real import or anything the
    user added afterwards. Shared entities (accounts, vendors) are removed only when
    nothing outside the demo still references them. Manual FX rates seeded for the
    demo's foreign rows are left in place (harmless, and they help real foreign
    spend on the same dates). Idempotent: with no manifest there is nothing to do."""
    raw = settings_service.get(db, settings_service.DEMO_MANIFEST)
    if not raw:
        return {"removed": False, "counts": {}}
    manifest: dict[str, list[int]] = json.loads(raw)
    counts: dict[str, int] = {}

    # 1. Transactions on the demo statements (derived — not stored in the manifest).
    _remove_demo_transactions(db, manifest, counts)

    # 1b. Demo receipts (row + stored file); their match rows already cascaded above.
    _remove_demo_receipts(db, manifest, counts)

    # 2. Savings goals (before any savings account they point at), then the uniquely
    #    demo budgets / projects / rules.
    counts["savings_goals"] = _bulk_delete(db, SavingsGoal, manifest.get("savings_goals", []))
    counts["budgets"] = _bulk_delete(db, Budget, manifest.get("budgets", []))
    counts["projects"] = _bulk_delete(db, Project, manifest.get("projects", []))
    counts["rules"] = _bulk_delete(db, Rule, manifest.get("rules", []))
    # Subscriptions detected from the demo's recurring transactions (vendor_id is
    # FK SET NULL, so order vs vendors doesn't matter).
    counts["subscriptions"] = _bulk_delete(db, Subscription, manifest.get("subscriptions", []))
    # Demo cars/home assets — their refuel/reading logs cascade (FK ON DELETE).
    counts["assets"] = _bulk_delete(db, Asset, manifest.get("assets", []))

    # 3. Accounts (Curve / Sam's Card / Emergency Fund + the investment & pension
    #    accounts) — only when no transaction is left on them, so an account a real
    #    import also used is kept. Savings balance snapshots, and investment holdings
    #    / value snapshots / price history, all cascade (FK ON DELETE).
    counts["accounts"] = _delete_if_unreferenced(
        db, Account, manifest.get("accounts", []), Transaction.account_id
    )

    # 4. Vendors — only those no surviving transaction still links to (demo txns are
    #    gone, so demo vendors are now unreferenced). Aliases cascade (FK ON DELETE).
    counts["vendors"] = _delete_if_unreferenced(
        db, Vendor, manifest.get("vendors", []), Transaction.merchant_id
    )

    # 5. Demo users last (cascades any remaining allocations; owned accounts/budgets
    #    were handled above and otherwise FK SET NULL).
    removed_users = 0
    for u_id in manifest.get("users", []):
        user = db.get(User, u_id)
        if user is None:
            continue
        db.delete(user)
        removed_users += 1
    counts["users"] = removed_users

    # The manifest is spent; clear it and undo the demo's DEBUG logging default.
    _reset_demo_settings(db)

    db.commit()
    return {"removed": True, "counts": counts}
