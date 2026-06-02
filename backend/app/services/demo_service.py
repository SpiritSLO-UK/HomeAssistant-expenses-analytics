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
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transaction
from app.services import fx_service, import_service, settings_service

# Representative GBP-per-1-unit rates for the demo's foreign spend. Manual rates
# are seeded for each foreign row's date so the rows convert to base (otherwise a
# manual-mode import leaves them needs_rate=True / out of every total).
_DEMO_FX_RATES = {"EUR": Decimal("0.86"), "USD": Decimal("0.79")}

# Number of monthly cycles to generate (current month + previous two).
_CYCLES = 3
# Days between cycles (≈ a month; keeps rows in distinct months without calendar
# arithmetic per row — see _spec_date).
_CYCLE_DAYS = 30


@dataclass(frozen=True)
class _Spec:
    """A demo transaction before it becomes a CSV row + optional enrichment."""

    cycle: int  # 0 = this month, 1 = last month, 2 = two months ago
    day: int  # day offset within the cycle (0..28)
    description: str
    amount: Decimal  # signed; negative = spend
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
    (25, "ODEON CINEMA", "21.00", False),
]
# (day, description, amount) — money in.
_MONTHLY_INCOME: list[tuple[int, str, str]] = [
    (0, "SALARY ACME LTD", "2450.00"),
    (28, "BARCLAYS INTEREST", "3.20"),
]

# --- One-off business expenses (GBP) with VAT, by cycle ----------------------
# (cycle, day, description, amount, vat)
_BUSINESS: list[tuple[int, int, str, str, str]] = [
    (0, 16, "TRAINLINE RAIL TICKET", "84.00", "14.00"),
    (0, 20, "AMAZON OFFICE SUPPLIES", "54.00", "9.00"),
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


def load_demo(db: Session) -> dict:
    """Import + enrich the demo dataset. Idempotent within a day (duplicates are
    skipped); returns the import report."""
    today = date.today()
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
    return confirmed["report"]
