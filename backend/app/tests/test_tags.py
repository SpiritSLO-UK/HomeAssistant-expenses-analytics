"""Tag tests (spec §18.3, §12.13 — Stage 5)."""

from __future__ import annotations


def _curve(rows: list[tuple[str, str, str]]) -> bytes:
    head = "Date,Description,Amount,Currency,Card,Category\n"
    body = "".join(f"{d},{desc},{amt},GBP,Visa,\n" for d, desc, amt in rows)
    return (head + body).encode()


def _import(client, content: bytes, name: str = "tags.csv"):
    up = client.post(
        "/api/imports/upload",
        files={"file": (name, content, "text/csv")},
        data={"parser_id": "curve_csv"},
    ).json()
    return client.post(f"/api/imports/{up['import_id']}/confirm").json()


def _txn(client, desc: str) -> dict:
    return next(
        t for t in client.get("/api/transactions").json()["items"] if t["description_raw"] == desc
    )


# --- CRUD ---

def test_tag_crud(client):
    res = client.post("/api/tags", json={"name": "reimbursable", "colour": "#3366ff"})
    assert res.status_code == 201, res.text
    tid = res.json()["id"]
    assert client.patch(f"/api/tags/{tid}", json={"colour": "#ff0000"}).json()["colour"] == "#ff0000"
    assert any(t["id"] == tid for t in client.get("/api/tags").json())
    assert client.delete(f"/api/tags/{tid}").status_code == 204


def test_tag_get_or_create_is_case_insensitive(client):
    client.post("/api/tags", json={"name": "Work"})
    # Assigning "work" should reuse the existing tag, not create a duplicate.
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-10.00")]))
    txn = _txn(client, "TESCO STORES")
    client.post(f"/api/transactions/{txn['id']}/tags", json={"tags": ["work"]})
    names = [t["name"] for t in client.get("/api/tags").json()]
    assert names.count("Work") == 1
    assert "work" not in names  # the original casing is kept


# --- assignment ---

def test_set_and_show_transaction_tags(client):
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-10.00")]))
    txn = _txn(client, "TESCO STORES")
    res = client.post(f"/api/transactions/{txn['id']}/tags", json={"tags": ["gift", "urgent"]})
    assert res.status_code == 200
    assert {t["name"] for t in res.json()["tags"]} == {"gift", "urgent"}

    # tags also surface on the list endpoint
    listed = _txn(client, "TESCO STORES")
    assert {t["name"] for t in listed["tags"]} == {"gift", "urgent"}

    # reassigning replaces
    client.post(f"/api/transactions/{txn['id']}/tags", json={"tags": ["warranty"]})
    assert {t["name"] for t in _txn(client, "TESCO STORES")["tags"]} == {"warranty"}


def test_filter_transactions_by_tag(client):
    _import(client, _curve([("2026-05-02", "TESCO STORES", "-10.00"),
                            ("2026-05-03", "SHELL FUEL", "-20.00")]))
    shell = _txn(client, "SHELL FUEL")
    client.post(f"/api/transactions/{shell['id']}/tags", json={"tags": ["work"]})
    tid = next(t["id"] for t in client.get("/api/tags").json() if t["name"] == "work")
    items = client.get(f"/api/transactions?tag_id={tid}").json()["items"]
    assert len(items) == 1
    assert items[0]["description_raw"] == "SHELL FUEL"
