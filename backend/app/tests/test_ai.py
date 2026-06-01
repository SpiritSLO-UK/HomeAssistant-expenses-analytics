"""AI gateway tests (spec §22 — Stage 9).

No real LLM: a fake provider is injected into the gateway, so we test gating,
redaction, auditing, the approval flow and the never-override guarantee without
any network call.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import AIRequest, Category, Transaction
from app.services import ai_service
from app.services.ai_service import AIDisabled


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, category="Groceries", confidence=0.9):
        self.category = category
        self.confidence = confidence
        self.calls: list[dict] = []

    def available(self) -> bool:
        return True

    def classify_transaction(self, description, amount, currency, candidate_categories):
        self.calls.append({"description": description, "candidate_categories": candidate_categories})
        return {"category": self.category, "confidence": self.confidence, "rationale": "because"}


def _curve(rows):
    head = "Date,Description,Amount,Currency,Card,Category\n"
    return (head + "".join(f"{d},{desc},{amt},GBP,Visa,\n" for d, desc, amt in rows)).encode()


def _import_txn(client, desc="ZZQ MARKET", amt="-12.00"):
    up = client.post("/api/imports/upload", files={"file": ("a.csv", _curve([("2026-05-02", desc, amt)]), "text/csv")},
                     data={"parser_id": "curve_csv"}).json()
    client.post(f"/api/imports/{up['import_id']}/confirm")
    return client.get("/api/transactions").json()["items"][0]["id"]


def _set_mode(client, mode, base="http://localhost:11434/v1", model="llama3"):
    client.put("/api/settings", json={
        "privacy_mode": mode, "ai_provider": "openai_compatible",
        "ai_base_url": base, "ai_model": model,
    })


def _classify(txn_id, **kwargs):
    with SessionLocal() as db:
        txn = db.get(Transaction, txn_id)
        return ai_service.classify_transaction(db, txn, **kwargs)


# --- gating ---

def test_ai_off_by_default(client):
    txn_id = _import_txn(client)
    r = client.post(f"/api/ai/classify/{txn_id}")  # privacy_mode defaults to strict_local
    assert r.status_code == 400
    assert client.get("/api/ai/status").json()["enabled"] is False


def test_status_reflects_settings(client):
    _set_mode(client, "local_llm")
    st = client.get("/api/ai/status").json()
    assert st["enabled"] is True
    assert st["is_cloud"] is False
    assert st["model"] == "llama3"


# --- local classification (suggestion only) ---

def test_local_classify_suggests_without_applying(client):
    txn_id = _import_txn(client)  # ZZQ MARKET -> uncategorised
    _set_mode(client, "local_llm")
    fake = FakeProvider(category="Groceries", confidence=0.8)
    res = _classify(txn_id, provider=fake)

    assert res["status"] == "ok"
    assert res["category_name"] == "Groceries"
    assert res["confidence"] == 0.8
    # AI must NOT apply the category itself (spec §22.1).
    assert client.get(f"/api/transactions/{txn_id}").json()["category_id"] is None
    # audited as completed (spec §22.6)
    reqs = client.get("/api/ai/requests").json()
    assert reqs[0]["status"] == "completed"
    assert reqs[0]["task_type"] == "classify_transaction"
    assert reqs[0]["privacy_mode"] == "local_llm"


def test_unknown_category_name_maps_to_none(client):
    txn_id = _import_txn(client)
    _set_mode(client, "local_llm")
    res = _classify(txn_id, provider=FakeProvider(category="Not A Real Category"))
    assert res["category_id"] is None


# --- cloud redaction + approval (spec §22.4, §22.5) ---

def test_cloud_payload_is_redacted(client):
    txn_id = _import_txn(client, desc="CARD 4111 1111 1111 1111 PAYMENT")
    _set_mode(client, "cloud_auto")
    fake = FakeProvider()
    _classify(txn_id, provider=fake)  # cloud_auto runs directly
    sent = fake.calls[0]["description"]
    assert "[card]" in sent          # redacted before leaving the device
    assert "4111 1111 1111" not in sent


def test_cloud_manual_approval_workflow(client):
    txn_id = _import_txn(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider()

    first = _classify(txn_id, provider=fake)
    assert first["status"] == "approval_required"
    assert first["payload"] is not None  # user can preview before sending
    assert fake.calls == []  # nothing sent yet
    assert "cloud_ai_approval_required" in {i["reason"] for i in client.get("/api/review?status=open").json()}

    # Approve the SAME pending request -> it sends, stores, resolves the review item.
    rid = first["ai_request_id"]
    with SessionLocal() as db:
        req = db.get(AIRequest, rid)
        approved = ai_service.run_request(db, req, provider=fake)
    assert approved["status"] == "ok"
    assert approved["transaction_id"] == txn_id
    assert len(fake.calls) == 1
    assert "cloud_ai_approval_required" not in {i["reason"] for i in client.get("/api/review?status=open").json()}


def test_never_cloud_category_blocks_cloud(client):
    txn_id = _import_txn(client)
    _set_mode(client, "cloud_auto")
    with SessionLocal() as db:
        txn = db.get(Transaction, txn_id)
        cat = db.scalars(select(Category)).first()
        cat.privacy_sensitivity = "never_cloud"
        txn.category_id = cat.id
        db.commit()
        with pytest.raises(AIDisabled):
            ai_service.classify_transaction(db, db.get(Transaction, txn_id), provider=FakeProvider())


def test_reject_pending_request(client):
    txn_id = _import_txn(client)
    _set_mode(client, "cloud_manual")
    rid = _classify(txn_id, provider=FakeProvider())["ai_request_id"]
    r = client.post(f"/api/ai/requests/{rid}/reject")
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert "cloud_ai_approval_required" not in {i["reason"] for i in client.get("/api/review?status=open").json()}


def test_approve_unknown_request_404(client):
    assert client.post("/api/ai/requests/9999/approve").status_code == 404


def test_invalid_privacy_mode_rejected(client):
    assert client.put("/api/settings", json={"privacy_mode": "telepathy"}).status_code == 400


# --- batch auto-apply (local only) ---

def _import_rows(client, rows):
    up = client.post("/api/imports/upload", files={"file": ("b.csv", _curve(rows), "text/csv")},
                     data={"parser_id": "curve_csv"}).json()
    client.post(f"/api/imports/{up['import_id']}/confirm")


def test_classify_batch_local_suggests_without_applying(client):
    _import_rows(client, [("2026-05-02", "ZZQ MARKET", "-12.00"), ("2026-05-03", "QQX DEPOT", "-8.00")])
    _set_mode(client, "local_llm")
    with SessionLocal() as db:
        res = ai_service.classify_batch(db, provider=FakeProvider(category="Groceries", confidence=0.9))
    assert res["considered"] == 2
    assert res["count"] == 2
    # Nothing applied yet — suggestions only.
    assert all(t["category_id"] is None for t in client.get("/api/transactions").json()["items"])


def test_classify_batch_refused_in_cloud(client):
    _import_rows(client, [("2026-05-02", "ZZQ MARKET", "-12.00")])
    _set_mode(client, "cloud_auto")
    assert client.post("/api/ai/classify-batch").status_code == 400  # local_llm only


def test_apply_suggestions_endpoint(client):
    txn_id = _import_txn(client)
    groceries = next(c["id"] for c in client.get("/api/categories").json() if c["name"] == "Groceries")
    r = client.post("/api/ai/apply", json={"items": [{"transaction_id": txn_id, "category_id": groceries}]})
    assert r.json()["applied"] == 1
    assert client.get(f"/api/transactions/{txn_id}").json()["category_id"] == groceries
