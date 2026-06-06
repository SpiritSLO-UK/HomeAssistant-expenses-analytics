#!/usr/bin/env python3
"""Stress-test seeder — bulk-load a large number of transactions to size-test the
database and see how the UI copes (pagination, dashboards, search) with millions
of rows.

DEV / BENCH ONLY. Not shipped in the add-on image (``scripts/`` is .dockerignored)
and not part of the app. It writes **directly** to the database for speed
(``bulk_insert_mappings``), so point ``HAFI_DATABASE_PATH`` at the instance you
want to fill — ideally a throwaway copy, not your real data.

All rows go on a dedicated **"Stress Test"** account so you can remove them
cleanly with ``--clear`` (or by merging/deleting that account in the UI).

Usage (from the repo root, in the backend venv):

    HAFI_DATABASE_PATH=./_stress.db backend/.venv/Scripts/python.exe scripts/stress_seed.py --count 1000000
    HAFI_DATABASE_PATH=./_stress.db backend/.venv/Scripts/python.exe scripts/stress_seed.py --clear
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from datetime import date, timedelta
from decimal import Decimal

# Make the backend package importable when run from the repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))

import app.models  # noqa: E402,F401  (register every model on the metadata)
from app.config import settings  # noqa: E402
from app.db import session as dbsession  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models import Account, Category, Transaction  # noqa: E402
from app.services.household_service import get_or_create_default_household  # noqa: E402
from sqlalchemy import delete, func, select  # noqa: E402

STRESS_ACCOUNT = "Stress Test"
_VENDORS = [
    "Tesco", "Sainsbury's", "Amazon", "Shell", "BP", "Netflix", "Spotify", "Costa",
    "Greggs", "Aldi", "Lidl", "Uber", "Deliveroo", "Apple", "Google", "Argos",
    "IKEA", "Boots", "Screwfix", "British Gas", "Thames Water", "Vodafone", "EE",
    "PureGym", "Pret", "McDonald's", "Steam", "PayPal", "Trainline", "Ryanair",
]


def _open_db():
    """Init the engine (handles plaintext/encrypted) and ensure tables exist."""
    dbsession.init()
    if dbsession.is_locked():
        sys.exit("Database is encrypted and locked — unlock it first (or use a plaintext copy).")
    Base.metadata.create_all(bind=dbsession.require_engine())


def _stress_account(db) -> Account:
    acc = db.scalars(select(Account).where(Account.name == STRESS_ACCOUNT)).first()
    if acc is None:
        acc = Account(
            name=STRESS_ACCOUNT,
            institution="Benchmark",
            account_type="current_account",
            currency="GBP",
            household_id=get_or_create_default_household(db).id,
        )
        db.add(acc)
        db.commit()
        db.refresh(acc)
    return acc


def seed(count: int, batch: int) -> None:
    _open_db()
    rng = random.Random(1234)  # deterministic, so reruns look the same
    with dbsession.SessionLocal() as db:
        acc = _stress_account(db)
        household_id = acc.household_id
        category_ids = list(db.scalars(select(Category.id).where(Category.is_active.is_(True))).all())
        # Continue ids after any existing stress rows so source_hash stays unique.
        start = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.account_id == acc.id)) or 0
        today = date.today()

        print(f"Seeding {count:,} transactions onto account '{STRESS_ACCOUNT}' (id={acc.id}) "
              f"in batches of {batch:,}…")
        t0 = time.time()
        done = 0
        while done < count:
            n = min(batch, count - done)
            rows = []
            for k in range(n):
                i = start + done + k
                credit = rng.random() < 0.08  # ~8% income/credits
                amount = Decimal(str(round(rng.uniform(2, 500), 2)))
                signed = amount if credit else -amount
                vendor = rng.choice(_VENDORS)
                rows.append({
                    "household_id": household_id,
                    "account_id": acc.id,
                    "transaction_date": today - timedelta(days=rng.randint(0, 1095)),
                    "description_raw": f"{vendor} #{i}",
                    "merchant_raw": vendor,
                    "amount": signed,
                    "currency": "GBP",
                    "direction": "credit" if credit else "debit",
                    "category_id": rng.choice(category_ids) if category_ids else None,
                    "base_amount": signed,
                    "fx_rate": Decimal("1"),
                    "source_hash": f"stress-{i}",
                    # bulk_insert_mappings bypasses Python-side defaults, so set the
                    # NOT-NULL booleans + currency explicitly (timestamps are
                    # server_default and fill themselves).
                    "is_split": False,
                    "is_transfer": False,
                    "is_income": credit,
                    "is_duplicate": False,
                    "is_business": False,
                    "needs_review": False,
                    "needs_rate": False,
                })
            db.bulk_insert_mappings(Transaction, rows)
            db.commit()
            done += n
            rate = done / max(time.time() - t0, 1e-6)
            print(f"  {done:,}/{count:,}  ({rate:,.0f} rows/s)")

        total = db.scalar(select(func.count()).select_from(Transaction)) or 0
    elapsed = time.time() - t0
    print(f"Done: inserted {count:,} in {elapsed:,.1f}s. Transactions table now holds {total:,} rows.")
    _report_db_size()


def clear() -> None:
    _open_db()
    with dbsession.SessionLocal() as db:
        acc = db.scalars(select(Account).where(Account.name == STRESS_ACCOUNT)).first()
        if acc is None:
            print("No 'Stress Test' account — nothing to clear.")
            return
        n = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.account_id == acc.id)) or 0
        db.execute(delete(Transaction).where(Transaction.account_id == acc.id))
        db.delete(acc)
        db.commit()
        print(f"Cleared {n:,} stress transactions and removed the '{STRESS_ACCOUNT}' account.")
    _report_db_size()


def _report_db_size() -> None:
    path = settings.database_path
    try:
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"Database file {path}: {size_mb:,.1f} MB")
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk-seed transactions to stress-test the DB + UI.")
    parser.add_argument("--count", type=int, default=1_000_000, help="how many transactions to insert")
    parser.add_argument("--batch", type=int, default=25_000, help="rows per bulk insert + commit")
    parser.add_argument("--clear", action="store_true", help="remove all stress data instead of seeding")
    args = parser.parse_args()
    if args.clear:
        clear()
    else:
        seed(args.count, args.batch)


if __name__ == "__main__":
    main()
