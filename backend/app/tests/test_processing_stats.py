"""Tests for the dashboard processing-stats card (backlog: "status of files
uploaded/processed, how many went through AI vs locally, AI timings")."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def test_processing_endpoint_after_demo(client):
    """The demo imports statements/transactions and seeds an attached receipt, but
    never calls AI or OCR, so the card reflects a fully-local pipeline."""
    client.post("/api/backup/demo")
    s = client.get("/api/dashboard/processing").json()
    assert s["statements_imported"] >= 1
    assert s["transactions_imported"] > 0
    assert s["receipts_total"] >= 1  # the demo seeds a receipt attached to a txn
    assert s["ai_total"] == 0
    assert s["ai_cloud"] == 0 and s["ai_local"] == 0
    assert s["ai_avg_seconds"] is None


def test_processing_stats_counts_ai_and_receipts(db):
    """AI calls are tallied by status, cloud-vs-local privacy mode and task, with an
    average turnaround; receipts are tallied by OCR status."""
    from app.models import AIRequest, Receipt
    from app.services import dashboard_service

    base = datetime(2026, 1, 1, 12, 0, 0)
    db.add_all(
        [
            AIRequest(provider="openai", task_type="classify_transaction", privacy_mode="cloud_manual",
                      status="completed", created_at=base, completed_at=base + timedelta(seconds=5)),
            AIRequest(provider="ollama", task_type="parse_receipt", privacy_mode="local_llm",
                      status="completed", created_at=base, completed_at=base + timedelta(seconds=3)),
            AIRequest(provider="openai", task_type="classify_transaction", privacy_mode="cloud_auto",
                      status="failed", created_at=base),
            AIRequest(provider="ollama", task_type="classify_transaction", privacy_mode="strict_local",
                      status="pending", created_at=base),
        ]
    )
    db.add_all(
        [
            Receipt(source_filename="r1.jpg", ocr_status="processed"),
            Receipt(source_filename="r2.jpg", ocr_status="failed"),
            Receipt(source_filename="r3.jpg", ocr_status="not_processed"),
        ]
    )
    db.commit()

    s = dashboard_service.processing_stats(db)

    assert s["ai_total"] == 4
    assert (s["ai_completed"], s["ai_failed"], s["ai_pending"]) == (2, 1, 1)
    assert s["ai_cloud"] == 2 and s["ai_local"] == 2
    # Average over the two completed calls only: (5 + 3) / 2.
    assert s["ai_avg_seconds"] == pytest.approx(4.0)
    assert s["ai_by_task"]["classify_transaction"] == 3
    assert s["ai_by_task"]["parse_receipt"] == 1

    assert s["receipts_total"] == 3
    assert (s["receipts_processed"], s["receipts_failed"], s["receipts_pending"]) == (1, 1, 1)


def test_processing_stats_empty_db(db):
    """A clean database returns all-zero counts and no average."""
    from app.services import dashboard_service

    s = dashboard_service.processing_stats(db)
    assert s["statements_imported"] == 0
    assert s["transactions_imported"] == 0
    assert s["ai_total"] == 0
    assert s["ai_avg_seconds"] is None
    assert s["ai_by_task"] == {}
