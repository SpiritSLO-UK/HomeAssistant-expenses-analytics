"""Tests for the Settings 'Storage & statistics' card (stats_service.system_stats).

The card computes only the import counts + AI tallies it displays, rather than
running the dashboard's full ``processing_stats`` and discarding the receipt OCR
breakdown / per-task tally it also computes. These tests pin the surfaced shape
and values so that optimisation stays behaviour-preserving.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest


def _txn(amount):
    from app.models import Transaction

    return Transaction(
        transaction_date=date(2026, 1, 1),
        description_raw="test",
        amount=amount,
        direction="debit",
    )


def test_system_stats_empty_db(db):
    """A clean database returns all-zero counts and no AI average."""
    from app.services import stats_service

    s = stats_service.system_stats(db)
    assert s["transactions"] == 0
    assert s["statements"] == 0
    assert s["receipts"] == 0
    assert s["ai_total"] == 0
    assert s["ai_cloud"] == 0 and s["ai_local"] == 0
    assert s["ai_completed"] == 0 and s["ai_failed"] == 0
    assert s["ai_avg_seconds"] is None
    assert "database_bytes" in s


def test_system_stats_counts_imports_and_ai(db):
    """Import counts and AI tallies match the underlying rows; only 'imported'
    statements count, and receipts are counted regardless of OCR status."""
    from app.models import AIRequest, Receipt, Statement
    from app.services import stats_service

    base = datetime(2026, 1, 1, 12, 0, 0)
    db.add_all(
        [
            Statement(source_filename="a.pdf", status="imported"),
            Statement(source_filename="b.pdf", status="imported"),
            Statement(source_filename="c.pdf", status="pending"),  # excluded
        ]
    )
    db.add_all([_txn(1), _txn(2), _txn(3)])
    db.add_all(
        [
            Receipt(source_filename="r1.jpg", ocr_status="processed"),
            Receipt(source_filename="r2.jpg", ocr_status="not_processed"),
        ]
    )
    db.add_all(
        [
            AIRequest(provider="openai", task_type="classify_transaction", privacy_mode="cloud_manual",
                      status="completed", created_at=base, completed_at=base + timedelta(seconds=5)),
            AIRequest(provider="ollama", task_type="parse_receipt", privacy_mode="local_llm",
                      status="completed", created_at=base, completed_at=base + timedelta(seconds=3)),
            AIRequest(provider="openai", task_type="classify_transaction", privacy_mode="cloud_auto",
                      status="failed", created_at=base),
        ]
    )
    db.commit()

    s = stats_service.system_stats(db)

    assert s["statements"] == 2  # only the two 'imported' ones
    assert s["transactions"] == 3
    assert s["receipts"] == 2
    assert s["ai_total"] == 3
    assert s["ai_cloud"] == 2 and s["ai_local"] == 1
    assert s["ai_completed"] == 2 and s["ai_failed"] == 1
    # Average over the two completed calls only: (5 + 3) / 2.
    assert s["ai_avg_seconds"] == pytest.approx(4.0)


def test_system_stats_matches_processing_stats_subset(db):
    """The surfaced values stay identical to the dashboard's full processing_stats
    for the fields this card exposes (single source of truth)."""
    from app.models import AIRequest, Receipt, Statement
    from app.services import dashboard_service, stats_service

    base = datetime(2026, 2, 1, 9, 0, 0)
    db.add(Statement(source_filename="s.pdf", status="imported"))
    db.add(_txn(10))
    db.add(Receipt(source_filename="r.jpg", ocr_status="failed"))
    db.add(
        AIRequest(provider="ollama", task_type="parse_receipt", privacy_mode="strict_local",
                  status="completed", created_at=base, completed_at=base + timedelta(seconds=2))
    )
    db.commit()

    s = stats_service.system_stats(db)
    proc = dashboard_service.processing_stats(db)

    assert s["transactions"] == proc["transactions_imported"]
    assert s["statements"] == proc["statements_imported"]
    assert s["receipts"] == proc["receipts_total"]
    assert s["ai_total"] == proc["ai_total"]
    assert s["ai_cloud"] == proc["ai_cloud"]
    assert s["ai_local"] == proc["ai_local"]
    assert s["ai_completed"] == proc["ai_completed"]
    assert s["ai_failed"] == proc["ai_failed"]
    assert s["ai_avg_seconds"] == proc["ai_avg_seconds"]


def test_table_row_counts_empty_db(db):
    """A clean database returns a zero count for every counted table and no
    largest table."""
    from app.services import stats_service

    result = stats_service.table_row_counts(db)

    assert "transactions" in result["counts"]
    assert "audit_logs" in result["counts"]
    assert all(n == 0 for n in result["counts"].values())
    assert result["largest_table"] is None


def test_table_row_counts_matches_seeded_rows_and_identifies_largest(db):
    """Per-table counts equal the seeded rows and the largest table is the one
    with the most rows."""
    from app.models import Account, Category, Vendor
    from app.services import stats_service

    # 3 transactions, 2 categories, 1 account, 0 vendors → categories is 2nd,
    # transactions is the clear largest.
    db.add_all([_txn(1), _txn(2), _txn(3)])
    db.add_all([Category(name="Groceries"), Category(name="Fuel")])
    db.add(Account(name="Checking"))
    db.commit()

    result = stats_service.table_row_counts(db)

    assert result["counts"]["transactions"] == 3
    assert result["counts"]["categories"] == 2
    assert result["counts"]["accounts"] == 1
    assert result["counts"]["vendors"] == 0
    assert result["largest_table"] == "transactions"

    # Adding more vendors flips the largest-table indicator.
    db.add_all([Vendor(canonical_name=f"V{i}") for i in range(5)])
    db.commit()
    assert stats_service.table_row_counts(db)["largest_table"] == "vendors"


def test_system_stats_surfaces_table_counts(db):
    """system_stats embeds the compact table_counts + largest_table so the
    Settings card can render the largest-table indicator."""
    from app.services import stats_service

    db.add_all([_txn(1), _txn(2)])
    db.commit()

    s = stats_service.system_stats(db)

    assert s["table_counts"]["transactions"] == 2
    assert s["largest_table"] == "transactions"
