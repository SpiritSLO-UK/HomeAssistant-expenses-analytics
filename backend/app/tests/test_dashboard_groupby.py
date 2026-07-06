"""SQL GROUP BY dashboard aggregates (OPT-1 / SR-B1).

These lock the perf refactor of ``dashboard_service.summary`` and
``category_breakdown`` — which push the heavy tallies into SQL conditional
aggregates + ``GROUP BY category`` — to the same outputs the all-in-Python
versions produced. The dataset deliberately mixes:

* a **split** transaction (its parts must land on their own categories, using
  the parent's ``fx_rate``/``base_amount`` — split-awareness preserved);
* a **foreign-currency** non-split row (``base_amount`` != ``amount``);
* an **archived** row (must be excluded from every aggregate);
* an **income** row (a credit — must never appear in the spend breakdown).

Money is compared with ``Decimal`` (never float ``==`` — S1244).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models import Category, Transaction, TransactionSplit
from app.services import dashboard_service

MONTH = date(2026, 5, 1)


def _txn(db, **kwargs) -> Transaction:
    defaults = dict(
        transaction_date=date(2026, 5, 15),
        description_raw="seed",
        currency="GBP",
        direction="debit",
    )
    defaults.update(kwargs)
    amt = defaults["amount"]
    defaults.setdefault("direction", "debit" if amt < 0 else "credit")
    t = Transaction(**defaults)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _seed(db) -> tuple[int, int]:
    cat_a = Category(name="Alpha", colour="#111111")
    cat_b = Category(name="Beta", colour="#222222")
    income_cat = Category(name="Income", colour="#333333")
    db.add_all([cat_a, cat_b, income_cat])
    db.commit()
    for c in (cat_a, cat_b, income_cat):
        db.refresh(c)

    # A foreign-currency split: EUR -100.00 @ 0.90 -> base -90.00, split 60/40.
    parent = _txn(
        db,
        amount=Decimal("-100.00"),
        currency="EUR",
        base_amount=Decimal("-90.00"),
        fx_rate=Decimal("0.90"),
        is_split=True,
        category_id=cat_a.id,  # own category is ignored once split, but still counted
    )
    db.add_all(
        [
            TransactionSplit(transaction_id=parent.id, category_id=cat_a.id, amount=Decimal("-60.00")),
            TransactionSplit(transaction_id=parent.id, category_id=cat_b.id, amount=Decimal("-40.00")),
        ]
    )
    db.commit()

    # A foreign-currency non-split row: USD -50.00 @ 0.80 -> base -40.00 (cat A).
    _txn(db, amount=Decimal("-50.00"), currency="USD",
         base_amount=Decimal("-40.00"), fx_rate=Decimal("0.80"), category_id=cat_a.id)
    # A plain GBP spend row (cat B).
    _txn(db, amount=Decimal("-10.00"), base_amount=Decimal("-10.00"),
         fx_rate=Decimal("1"), category_id=cat_b.id)
    # An income credit (must not appear in the spend breakdown).
    _txn(db, amount=Decimal("200.00"), base_amount=Decimal("200.00"),
         fx_rate=Decimal("1"), category_id=income_cat.id)
    # An archived spend row — excluded from every aggregate.
    _txn(db, amount=Decimal("-1000.00"), base_amount=Decimal("-1000.00"),
         fx_rate=Decimal("1"), category_id=cat_a.id,
         archived_at=datetime.now(UTC).replace(tzinfo=None))

    return cat_a.id, cat_b.id


def test_category_breakdown_groupby_matches_known_dataset(db):
    cat_a, cat_b = _seed(db)
    rows = {r["category_id"]: r for r in dashboard_service.category_breakdown(db, MONTH)}

    # cat A: split part 54.00 (=60*0.90) + foreign non-split 40.00 (=50*0.80).
    assert Decimal(rows[cat_a]["total"]) == Decimal("94.00")
    assert rows[cat_a]["count"] == 2
    # cat B: split part 36.00 (=40*0.90) + plain 10.00.
    assert Decimal(rows[cat_b]["total"]) == Decimal("46.00")
    assert rows[cat_b]["count"] == 2

    # Archived row (would have been the largest) is absent; income never appears.
    assert Decimal("1000.00") not in {Decimal(r["total"]) for r in rows.values()}
    assert sum(Decimal(r["total"]) for r in rows.values()) == Decimal("140.00")
    # Split-aware: parts land on their own categories, not the parent's.
    assert set(rows) == {cat_a, cat_b}
    # Rows come back sorted by spend descending.
    ordered = list(dashboard_service.category_breakdown(db, MONTH))
    assert [Decimal(r["total"]) for r in ordered] == sorted(
        (Decimal(r["total"]) for r in ordered), reverse=True
    )


def test_summary_conditional_aggregates_match_known_dataset(db):
    _seed(db)
    s = dashboard_service.summary(db, MONTH)

    # Spend uses the PARENT base_amount (splits sum to it): 90 + 40 + 10 = 140.
    assert Decimal(s["spend_this_month"]) == Decimal("140.00")
    assert Decimal(s["income_this_month"]) == Decimal("200.00")
    assert Decimal(s["net_this_month"]) == Decimal("60.00")
    # Counts are archived-excluded: split parent + foreign + gbp + income = 4.
    assert s["total_transactions"] == 4
    assert s["uncategorised_transactions"] == 0
    assert s["review_items"] == 0
    assert s["needs_rate"] == 0
