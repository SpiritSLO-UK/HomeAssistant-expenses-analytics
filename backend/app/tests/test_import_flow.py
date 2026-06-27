"""Integration tests for the import + transactions flow (spec §32.2).

Covers the Stage 1 acceptance criteria: upload a Curve CSV, transactions appear,
a duplicate upload does not duplicate transactions, and an import report is
returned.
"""

from __future__ import annotations


def _upload(client, samples_dir, filename: str, **form):
    content = (samples_dir / filename).read_bytes()
    return client.post(
        "/api/imports/upload",
        files={"file": (filename, content, "text/csv")},
        data=form,
    )


def test_list_parsers(client):
    res = client.get("/api/imports/parsers")
    assert res.status_code == 200
    ids = {p["parser_id"] for p in res.json()}
    assert {"curve_csv", "barclays_csv", "lloyds_csv", "monzo_csv", "generic_csv"} <= ids


def test_upload_preview_confirm_curve(client, samples_dir):
    res = _upload(client, samples_dir, "curve-sample.csv")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["detected_parser"] == "curve_csv"
    assert body["rows_detected"] == 8
    assert body["report"] == {"rows_detected": 8, "new": 8, "duplicates": 0, "errors": 0}
    assert len(body["preview"]) == 8
    assert body["preview"][0]["is_duplicate"] is False

    import_id = body["import_id"]
    confirm = client.post(f"/api/imports/{import_id}/confirm")
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["status"] == "imported"
    assert confirm.json()["report"]["new"] == 8

    txns = client.get("/api/transactions").json()
    assert txns["total"] == 8
    # Most recent first; salary is a credit.
    assert any(t["direction"] == "credit" for t in txns["items"])


def test_duplicate_upload_does_not_duplicate(client, samples_dir):
    # First import
    first = _upload(client, samples_dir, "curve-sample.csv").json()
    client.post(f"/api/imports/{first['import_id']}/confirm")

    # Second import of the same file
    second = _upload(client, samples_dir, "curve-sample.csv")
    body = second.json()
    assert body["report"]["duplicates"] == 8
    assert body["report"]["new"] == 0
    assert any("already imported" in w for w in body["warnings"])

    confirm = client.post(f"/api/imports/{body['import_id']}/confirm").json()
    assert confirm["report"]["duplicates"] == 8
    assert confirm["report"]["new"] == 0

    assert client.get("/api/transactions").json()["total"] == 8


def test_generic_upload(client, samples_dir):
    body = _upload(client, samples_dir, "generic-sample.csv").json()
    assert body["detected_parser"] == "generic_csv"
    client.post(f"/api/imports/{body['import_id']}/confirm")
    txns = client.get("/api/transactions").json()
    assert txns["total"] == 5
    # Refund row is a credit (money in).
    assert sum(1 for t in txns["items"] if t["direction"] == "credit") == 1


def test_transaction_filters_and_patch(client, samples_dir):
    body = _upload(client, samples_dir, "curve-sample.csv").json()
    client.post(f"/api/imports/{body['import_id']}/confirm")

    # Search filter
    res = client.get("/api/transactions", params={"search": "TESCO"}).json()
    assert res["total"] == 1
    txn_id = res["items"][0]["id"]

    # Patch: mark as transfer
    patched = client.patch(f"/api/transactions/{txn_id}", json={"is_transfer": True})
    assert patched.status_code == 200
    assert patched.json()["is_transfer"] is True

    # Amount filter (debits only)
    debits = client.get("/api/transactions", params={"amount_max": "0"}).json()
    assert debits["total"] == 7  # 8 rows, 1 is the salary credit


def test_delete_pending_import(client, samples_dir):
    body = _upload(client, samples_dir, "monzo-sample.csv").json()
    import_id = body["import_id"]
    assert client.delete(f"/api/imports/{import_id}").status_code == 204
    assert client.get(f"/api/imports/{import_id}").status_code == 404


def test_statement_config_tolerates_malformed_notes():
    # confirm/delete read the parser config from statement.notes; a non-JSON or
    # non-dict value must degrade to {} rather than crash (SR-A1).
    from types import SimpleNamespace

    from app.services import import_service as imp

    assert imp._statement_config(SimpleNamespace(notes=None)) == {}
    assert imp._statement_config(SimpleNamespace(notes="")) == {}
    assert imp._statement_config(SimpleNamespace(notes="legacy free text")) == {}
    assert imp._statement_config(SimpleNamespace(notes='["a", "list"]')) == {}  # JSON, not a dict
    assert imp._statement_config(SimpleNamespace(notes='{"parser_id": "curve_csv"}')) == {
        "parser_id": "curve_csv"
    }
