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


def test_import_profile_persists_date_format(client):
    # date_format defaults to "auto" and round-trips through create/read/update.
    created = client.post(
        "/api/imports/profiles",
        json={"name": "USBank", "mapping": {"date": "Date", "amount": "Amount"}},
    ).json()
    assert created["date_format"] == "auto"
    pid = created["id"]

    upd = client.put(
        f"/api/imports/profiles/{pid}",
        json={"name": "USBank", "mapping": {"date": "Date", "amount": "Amount"}, "date_format": "mdy"},
    ).json()
    assert upd["date_format"] == "mdy"
    assert next(p for p in client.get("/api/imports/profiles").json() if p["id"] == pid)["date_format"] == "mdy"


def test_import_profile_rejects_invalid_date_format(client):
    r = client.post(
        "/api/imports/profiles",
        json={"name": "Bad", "mapping": {"date": "Date", "amount": "Amount"}, "date_format": "iso"},
    )
    assert r.status_code == 422, r.text


def test_upload_date_format_forces_us_month_first(client):
    # Every day component is ≤ 12, so auto-detection has no evidence and stays
    # day-first; date_format="mdy" must force US month-first parsing.
    csv = b"Date,Amount,Description\n6/3/2026,-3.50,Coffee\n7/8/2026,-8.00,Lunch\n"
    files = {"file": ("us.csv", csv, "text/csv")}
    mapping = json.dumps({"date": "Date", "amount": "Amount", "description": "Description"})

    auto = client.post("/api/imports/upload", files=files,
                       data={"parser_id": "generic_csv", "mapping": mapping}).json()
    assert auto["preview"][0]["transaction_date"] == "2026-03-06"  # misread day-first

    us = client.post("/api/imports/upload", files=files,
                     data={"parser_id": "generic_csv", "mapping": mapping, "date_format": "mdy"}).json()
    assert us["preview"][0]["transaction_date"] == "2026-06-03"  # June 3, month-first
    assert us["preview"][1]["transaction_date"] == "2026-07-08"  # July 8


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
