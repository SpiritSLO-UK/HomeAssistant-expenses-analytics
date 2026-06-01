"""Cloud batch AI categorisation (Stage 12; backlog #154, spec §22.3, §22.5).

A fake provider is injected at the service layer, so we test the two-stage
approval (prepare = redact + audit pending, nothing sent → send = run approved,
reject the rest) without any network call.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import AIRequest, Transaction
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
        self.calls.append({"description": description})
        return {"category": self.category, "confidence": self.confidence, "rationale": "because"}


def _curve(rows):
    head = "Date,Description,Amount,Currency,Card,Category\n"
    return (head + "".join(f"{d},{desc},{amt},GBP,Visa,\n" for d, desc, amt in rows)).encode()


def _import_rows(client, rows):
    up = client.post(
        "/api/imports/upload",
        files={"file": ("b.csv", _curve(rows), "text/csv")},
        data={"parser_id": "curve_csv"},
    ).json()
    client.post(f"/api/imports/{up['import_id']}/confirm")


def _set_mode(client, mode):
    client.put("/api/settings", json={
        "privacy_mode": mode, "ai_provider": "openai_compatible",
        "ai_base_url": "http://localhost:11434/v1", "ai_model": "llama3",
    })


def _two_uncategorised(client):
    _import_rows(client, [("2026-05-02", "ZZQ MARKET", "-12.00"), ("2026-05-03", "QQX DEPOT", "-8.00")])


def test_prepare_audits_pending_and_sends_nothing(client):
    _two_uncategorised(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider()
    with SessionLocal() as db:
        res = ai_service.cloud_batch_prepare(db, provider=fake)

    assert res["considered"] == 2
    assert res["count"] == 2
    assert all("candidate_categories" in i["payload"] for i in res["items"])
    assert fake.calls == []  # nothing left the device

    with SessionLocal() as db:
        reqs = db.scalars(select(AIRequest)).all()
        assert len(reqs) == 2
        assert all(r.status == "pending" and r.approval_status == "pending" for r in reqs)
    # still uncategorised
    assert all(t["category_id"] is None for t in client.get("/api/transactions").json()["items"])


def test_send_runs_approved_and_rejects_the_rest(client):
    _two_uncategorised(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider(category="Groceries", confidence=0.95)

    with SessionLocal() as db:
        prepared = ai_service.cloud_batch_prepare(db, provider=fake)
    ids = [i["ai_request_id"] for i in prepared["items"]]

    with SessionLocal() as db:
        res = ai_service.cloud_batch_send(db, approve_ids=ids[:1], reject_ids=ids[1:], provider=fake)

    assert len(fake.calls) == 1  # only the approved one was sent
    assert res["count"] == 1
    assert res["rejected"] == 1
    assert res["suggestions"][0]["category_name"] == "Groceries"

    with SessionLocal() as db:
        approved = db.get(AIRequest, ids[0])
        rejected = db.get(AIRequest, ids[1])
        assert approved.status == "completed" and approved.approval_status == "approved"
        assert rejected.status == "rejected" and rejected.approval_status == "rejected"
    # suggestions only — nothing applied yet
    assert all(t["category_id"] is None for t in client.get("/api/transactions").json()["items"])


def test_prepare_payload_is_redacted(client):
    _import_rows(client, [("2026-05-02", "CARD 4111 1111 1111 1111 PAYMENT", "-9.00")])
    _set_mode(client, "cloud_manual")
    with SessionLocal() as db:
        res = ai_service.cloud_batch_prepare(db, provider=FakeProvider())
    desc = res["items"][0]["payload"]["description"]
    assert "[card]" in desc
    assert "4111 1111 1111" not in desc


def test_prepare_refused_outside_cloud_modes(client):
    _two_uncategorised(client)
    _set_mode(client, "local_llm")
    with SessionLocal() as db, pytest.raises(AIDisabled):
        ai_service.cloud_batch_prepare(db, provider=FakeProvider())


def test_prepare_endpoint_refused_in_local_mode(client):
    _two_uncategorised(client)
    _set_mode(client, "local_llm")
    assert client.post("/api/ai/cloud-batch/prepare").status_code == 400


def test_full_flow_applies_after_send(client):
    _two_uncategorised(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider(category="Groceries", confidence=0.9)

    with SessionLocal() as db:
        prepared = ai_service.cloud_batch_prepare(db, provider=fake)
        ids = [i["ai_request_id"] for i in prepared["items"]]
    with SessionLocal() as db:
        sent = ai_service.cloud_batch_send(db, approve_ids=ids, provider=fake)

    items = [{"transaction_id": s["transaction_id"], "category_id": s["category_id"]} for s in sent["suggestions"]]
    applied = client.post("/api/ai/apply", json={"items": items}).json()
    assert applied["applied"] == len(items)

    with SessionLocal() as db:
        groceries = next(
            t for t in db.scalars(select(Transaction)).all() if t.category_id is not None
        )
        assert groceries.confidence_score == 1.0  # treated as a manual decision
