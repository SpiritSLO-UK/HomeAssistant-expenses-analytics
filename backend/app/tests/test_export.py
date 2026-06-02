"""CSV export of transactions + dashboard summaries (Stage 12; backlog #132).

Uses the demo dataset to populate transactions, then exercises the export
endpoints: correct CSV shape/headers, the Excel BOM, and that the transactions
export honours the same filters as the list view (export == what you see).
"""

from __future__ import annotations

import csv
import io


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
