"""CSV export of transactions + dashboard summaries (Stage 12; backlog #132).

Uses the demo dataset to populate transactions, then exercises the export
endpoints: correct CSV shape/headers, the Excel BOM, and that the transactions
export honours the same filters as the list view (export == what you see).
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal


def _rows(resp) -> list[list[str]]:
    # decode utf-8-sig to transparently drop the BOM before parsing
    return list(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))


def test_transactions_csv_export(client):
    client.post("/api/backup/demo")
    resp = client.get("/api/export/transactions.csv")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    cd = resp.headers["content-disposition"]
    assert "attachment" in cd and "transactions-" in cd and cd.endswith('.csv"')

    rows = _rows(resp)
    assert rows[0][:5] == ["date", "posted_date", "description", "merchant", "amount"]
    assert rows[0][6:8] == ["base_amount", "base_currency"]
    assert len(rows) > 1  # header + at least one transaction


def test_transactions_csv_has_utf8_bom(client):
    client.post("/api/backup/demo")
    resp = client.get("/api/export/transactions.csv")
    assert resp.content.startswith(b"\xef\xbb\xbf")  # Excel-friendly UTF-8 BOM


def test_transactions_csv_export_matches_list(client):
    """The export is the full filtered set, not just one page."""
    client.post("/api/backup/demo")
    total = client.get("/api/transactions", params={"limit": 500}).json()["total"]
    rows = _rows(client.get("/api/export/transactions.csv"))
    assert len(rows) - 1 == total  # minus the header row


def test_uncategorised_filter_partitions_and_exports(client):
    """`uncategorised` filters on a missing category — distinct from the
    needs_review flag — and the CSV export honours it (export == what you see)."""
    client.post("/api/backup/demo")
    all_total = client.get("/api/transactions", params={"limit": 500}).json()["total"]
    unc = client.get("/api/transactions", params={"uncategorised": "true", "limit": 500}).json()["total"]
    cat = client.get("/api/transactions", params={"uncategorised": "false", "limit": 500}).json()["total"]
    assert unc > 0  # the demo seeds some uncategorised foreign purchases
    assert unc + cat == all_total  # every row is either categorised or not
    rows = _rows(client.get("/api/export/transactions.csv", params={"uncategorised": "true"}))
    assert len(rows) - 1 == unc


def test_transaction_id_filter_returns_one(client):
    """The focus deep-link (Review-Queue "Open transaction →", trip drill-down)
    narrows the list to a single transaction by id, so the linked row is always
    surfaced regardless of which page it would otherwise fall on."""
    client.post("/api/backup/demo")
    target = client.get("/api/transactions", params={"limit": 1}).json()["items"][0]["id"]

    resp = client.get("/api/transactions", params={"transaction_id": target}).json()
    assert resp["total"] == 1
    assert [t["id"] for t in resp["items"]] == [target]

    # An unknown id is an empty result, not an error.
    assert client.get("/api/transactions", params={"transaction_id": 999_999}).json()["total"] == 0


def test_transactions_csv_respects_filters(client):
    client.post("/api/backup/demo")
    full = _rows(client.get("/api/export/transactions.csv"))
    # An impossible date window → header only.
    empty = _rows(
        client.get(
            "/api/export/transactions.csv",
            params={"date_from": "1990-01-01", "date_to": "1990-01-02"},
        )
    )
    assert len(empty) == 1
    assert len(full) > len(empty)


def test_date_to_boundary_is_inclusive_whole_day(db):
    """``date_to`` is an inclusive-whole-day bound, implemented as the half-open
    ``< date_to + 1 day`` interval the rest of the codebase uses (dashboard/
    analytics/budget windows). So a transaction dated exactly on ``date_to`` is
    included and one dated the following day is excluded."""
    from app.models import Transaction
    from app.services import export_service

    def _mk(txn_date: date, desc: str) -> None:
        db.add(
            Transaction(
                transaction_date=txn_date,
                description_raw=desc,
                amount=Decimal("10.00"),
                direction="debit",
            )
        )

    end = date(2024, 3, 15)
    _mk(end, "on-date_to")
    _mk(date(2024, 3, 16), "day-after")  # one day past the inclusive bound
    db.commit()

    conditions = export_service.build_transaction_filters(date_to=end)
    csv_text = export_service.transactions_csv(db, conditions)
    rows = list(csv.reader(io.StringIO(csv_text)))
    descriptions = [r[2] for r in rows[1:]]  # column 2 = description

    assert "on-date_to" in descriptions  # exactly on date_to → included
    assert "day-after" not in descriptions  # the next day → excluded


def test_transactions_csv_export_with_ids_returns_only_selected(client):
    """Passing ``ids`` narrows the export to that ticked selection (and no other
    rows), while omitting it exports the whole filtered set unchanged."""
    client.post("/api/backup/demo")
    items = client.get("/api/transactions", params={"limit": 500}).json()["items"]
    full_count = len(_rows(client.get("/api/export/transactions.csv"))) - 1
    assert full_count == len(items)  # sanity: no-ids export is the full set

    chosen = items[:3]
    chosen_ids = [t["id"] for t in chosen]
    rows = _rows(client.get("/api/export/transactions.csv", params={"ids": chosen_ids}))

    assert len(rows) - 1 == len(chosen_ids)  # only the selected rows, plus header
    assert len(chosen_ids) < full_count  # and it is a strict subset
    exported_descs = sorted(r[2] for r in rows[1:])
    assert exported_descs == sorted(t["description_raw"] or "" for t in chosen)


def test_transactions_csv_export_ids_cannot_bypass_scope(db):
    """An ``ids`` selection is ANDed with the account scope, so it can never
    surface a row outside the caller's visibility (defence in depth: the id list
    comes straight from the client)."""
    from app.models import Account, Transaction
    from app.services import export_service

    def _mk(account_id: int, desc: str) -> Transaction:
        return Transaction(
            account_id=account_id, transaction_date=date(2026, 5, 15),
            description_raw=desc, amount=Decimal("10.00"), currency="GBP",
            direction="debit", base_amount=Decimal("10.00"), fx_rate=Decimal("1"),
        )

    a1 = Account(name="A1", account_type="current_account", currency="GBP")
    a2 = Account(name="A2", account_type="current_account", currency="GBP")
    db.add_all([a1, a2])
    db.flush()
    in_scope = _mk(a1.id, "IN_SCOPE")
    out_scope = _mk(a2.id, "OUT_OF_SCOPE")
    db.add_all([in_scope, out_scope])
    db.commit()

    # Select BOTH rows by id, but restrict visibility to account a1 only.
    conditions = export_service.build_transaction_filters(
        ids=[in_scope.id, out_scope.id], account_ids={a1.id}
    )
    rows = list(csv.reader(io.StringIO(export_service.transactions_csv(db, conditions))))
    descriptions = [r[2] for r in rows[1:]]

    assert descriptions == ["IN_SCOPE"]  # the out-of-scope id is filtered out


def test_categories_csv_export(client):
    client.post("/api/backup/demo")
    resp = client.get("/api/export/categories.csv")
    assert resp.status_code == 200
    rows = _rows(resp)
    assert rows[0] == ["category", "total", "currency", "transactions"]


def test_monthly_csv_export_respects_months(client):
    client.post("/api/backup/demo")
    resp = client.get("/api/export/monthly.csv", params={"months": 3})
    assert resp.status_code == 200
    rows = _rows(resp)
    assert rows[0] == ["month", "spend", "income", "net", "currency"]
    assert len(rows) - 1 <= 3  # at most `months` data rows
