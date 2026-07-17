"""Review queue tests (spec §23 — the queue receipts feed)."""

from __future__ import annotations


def _upload(client, content=b"unmatched-receipt", name="receipt.png"):
    return client.post("/api/receipts/upload", files={"file": (name, content, "image/png")})


def test_unmatched_receipt_files_a_review_item(client):
    # No transactions exist, so a manually-totalled receipt can't match.
    rid = _upload(client).json()["id"]
    client.patch(f"/api/receipts/{rid}", json={"total_amount": "12.34", "receipt_date": "2026-05-02"})
    res = client.post(f"/api/receipts/{rid}/match").json()
    assert res["status"] == "unmatched"

    items = client.get("/api/review?status=open").json()
    reasons = {i["reason"] for i in items}
    assert "receipt_unmatched" in reasons
    assert client.get("/api/review/count").json()["open"] >= 1


def test_resolve_review_item(client):
    rid = _upload(client).json()["id"]
    client.patch(f"/api/receipts/{rid}", json={"total_amount": "5.00"})
    client.post(f"/api/receipts/{rid}/match")
    item = client.get("/api/review?status=open").json()[0]

    resolved = client.patch(f"/api/review/{item['id']}", json={"status": "resolved"})
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert all(i["id"] != item["id"] for i in client.get("/api/review?status=open").json())


def test_review_rejects_bad_status(client):
    rid = _upload(client).json()["id"]
    client.patch(f"/api/receipts/{rid}", json={"total_amount": "5.00"})
    client.post(f"/api/receipts/{rid}/match")
    item = client.get("/api/review?status=open").json()[0]
    assert client.patch(f"/api/review/{item['id']}", json={"status": "nonsense"}).status_code == 400


def test_confirming_match_resolves_review(client):
    # Import a transaction, upload a receipt that won't auto-match (different
    # amount), then confirm manually -> its review item clears.
    head = "Date,Description,Amount,Currency,Card,Category\n2026-05-02,SHELL FUEL,-60.00,GBP,Visa,\n"
    up = client.post("/api/imports/upload", files={"file": ("a.csv", head.encode(), "text/csv")},
                     data={"parser_id": "curve_csv"}).json()
    client.post(f"/api/imports/{up['import_id']}/confirm")
    txn = client.get("/api/transactions").json()["items"][0]

    rid = _upload(client).json()["id"]
    client.patch(f"/api/receipts/{rid}", json={"total_amount": "999.00"})  # won't match
    client.post(f"/api/receipts/{rid}/match")
    assert client.get("/api/review/count").json()["open"] >= 1

    client.post(f"/api/receipts/{rid}/confirm-match", json={"transaction_id": txn["id"]})
    open_reasons = {i["reason"] for i in client.get("/api/review?status=open").json()}
    assert "receipt_unmatched" not in open_reasons


def test_set_status_rejects_unknown_status_at_service_level(db):
    # Defense-in-depth: even bypassing the route, the service refuses a status
    # that list_items/open_count couldn't see (SR-F9).
    import pytest

    from app.services import review_service

    item = review_service.add(db, item_type="vendor", item_id=1, reason="unknown_vendor")
    db.commit()
    with pytest.raises(ValueError, match="Unknown review status"):
        review_service.set_status(db, item, "nonsense")


def test_add_dedupes_within_a_single_unit_of_work(db):
    # SR-F9: two adds for the same (type, id, reason) in ONE unit of work (before
    # any commit) must collapse to a single open row, not create a duplicate. The
    # session runs autoflush=False, so this guards the per-call flush in add().
    from sqlalchemy import func, select

    from app.models import ReviewItem
    from app.services import review_service

    first = review_service.add(db, item_type="vendor", item_id=7, reason="unknown_vendor")
    second = review_service.add(db, item_type="vendor", item_id=7, reason="unknown_vendor")
    db.commit()

    assert first.id == second.id
    count = db.scalar(
        select(func.count())
        .select_from(ReviewItem)
        .where(ReviewItem.item_id == 7, ReviewItem.status == "open")
    )
    assert count == 1


def test_list_items_is_bounded_by_limit(db):
    # list_items must never return an unbounded result set: a small explicit limit
    # caps the rows, and the module default is finite.
    from app.services import review_service

    for n in range(5):
        review_service.add(db, item_type="vendor", item_id=100 + n, reason="unknown_vendor")
    db.commit()

    assert len(review_service.list_items(db, limit=2)) == 2
    assert review_service.DEFAULT_LIST_LIMIT >= 1
    # default (generous but finite) still returns everything for a small backlog
    assert len(review_service.list_items(db)) == 5
