"""Custom CSV column mapping + saved import profiles (user-defined CSV import).

The backend already parses a generic CSV with an explicit ``mapping``; these
cover the new pieces: the ``/inspect`` endpoint that drives the mapping UI, the
import-profile CRUD, and that a custom mapping actually imports an otherwise
unrecognised CSV.
"""

from __future__ import annotations

import json


def test_inspect_csv_returns_headers_sample_and_suggestion(client):
    csv = b"Date,Amount,Description\n2026-01-02,-5.00,Coffee\n2026-01-03,10.00,Refund\n"
    r = client.post("/api/imports/inspect", files={"file": ("x.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["headers"] == ["Date", "Amount", "Description"]
    assert data["suggested_mapping"]["date"] == "Date"
    assert data["suggested_mapping"]["amount"] == "Amount"
    assert len(data["sample_rows"]) == 2
    # The field catalogue is returned so the UI can render the form; date is required.
    assert any(f["key"] == "date" and f["required"] for f in data["fields"])


def test_inspect_rejects_non_csv(client):
    assert client.post("/api/imports/inspect", files={"file": ("x.csv", b"", "text/csv")}).status_code == 400


def test_import_profile_crud(client):
    payload = {
        "name": "MyBank",
        "mapping": {"date": "When", "amount": "Paid", "description": "Note", "bogus": "x"},
        "default_currency": "usd",
    }
    created = client.post("/api/imports/profiles", json=payload).json()
    assert created["name"] == "MyBank"
    assert created["mapping"]["date"] == "When"
    assert "bogus" not in created["mapping"]  # unknown logical fields are dropped
    assert created["default_currency"] == "USD"
    pid = created["id"]

    assert any(p["id"] == pid for p in client.get("/api/imports/profiles").json())
    # Duplicate name rejected.
    assert client.post("/api/imports/profiles", json=payload).status_code == 400

    upd = client.put(
        f"/api/imports/profiles/{pid}",
        json={"name": "MyBank 2", "mapping": {"date": "When", "amount": "Paid"}, "default_currency": "GBP"},
    ).json()
    assert upd["name"] == "MyBank 2"
    assert "description" not in upd["mapping"]

    assert client.delete(f"/api/imports/profiles/{pid}").status_code == 200
    assert client.delete(f"/api/imports/profiles/{pid}").status_code == 404


def test_import_with_custom_mapping_parses_unrecognised_csv(client):
    # Headers no built-in parser / heuristic recognises — only an explicit mapping works.
    csv = b"When,Paid,Note\n2026-01-02,-5.00,Coffee\n"
    mapping = {"date": "When", "amount": "Paid", "description": "Note"}
    r = client.post(
        "/api/imports/upload",
        files={"file": ("x.csv", csv, "text/csv")},
        data={"parser_id": "generic_csv", "mapping": json.dumps(mapping)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows_detected"] == 1
    row = body["preview"][0]
    assert row["description_raw"] == "Coffee"
    assert row["amount"].startswith("-5")
    assert row["direction"] == "debit"
