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


@pytest.fixture(autouse=True)
def _clear_inflight():
    """Isolate the module-level in-flight guard between tests (it's process-global,
    so a test that reserves ids must not bleed into the next)."""
    ai_service._inflight_batch_ids.clear()
    yield
    ai_service._inflight_batch_ids.clear()


def _prepare_ids(client, provider):
    with SessionLocal() as db:
        prepared = ai_service.cloud_batch_prepare(db, provider=provider)
    return [i["ai_request_id"] for i in prepared["items"]]


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


def test_send_refused_when_txn_category_now_never_cloud(client):
    """#19: a request staged in a cloud mode is re-validated at send time, exactly
    like run_request. If the txn's category has since been marked never-cloud, the
    batch send refuses it (records it failed) instead of dispatching the payload."""
    from app.models import Category

    _two_uncategorised(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider()
    with SessionLocal() as db:
        prepared = ai_service.cloud_batch_prepare(db, provider=fake)
    ids = [i["ai_request_id"] for i in prepared["items"]]
    # After staging, route the first request's txn into a never-cloud category.
    with SessionLocal() as db:
        req0 = db.get(AIRequest, ids[0])
        never = Category(name="Therapy", privacy_sensitivity="never_cloud", is_active=True)
        db.add(never)
        db.flush()
        db.get(Transaction, req0.transaction_id).category_id = never.id
        db.commit()

    with SessionLocal() as db:
        res = ai_service.cloud_batch_send(db, approve_ids=ids, provider=fake)

    assert ids[0] in res["failed"]
    assert len(fake.calls) == 1  # only the still-safe request left the device
    with SessionLocal() as db:
        assert db.get(AIRequest, ids[0]).status == "failed"


def test_send_refused_when_mode_now_off(client):
    """#19: if AI is switched off after staging, no stored cloud request is sent."""
    _two_uncategorised(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider()
    with SessionLocal() as db:
        prepared = ai_service.cloud_batch_prepare(db, provider=fake)
    ids = [i["ai_request_id"] for i in prepared["items"]]

    _set_mode(client, "strict_local")  # AI turned off between staging and sending
    with SessionLocal() as db:
        res = ai_service.cloud_batch_send(db, approve_ids=ids, provider=fake)

    assert fake.calls == []  # nothing dispatched
    assert set(res["failed"]) == set(ids)


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
        assert groceries.confidence_score == pytest.approx(1.0)  # treated as a manual decision


# --- non-blocking send: cloud_batch_start + run_cloud_batch + cloud_batch_status ---


def test_start_returns_promptly_and_dispatches_nothing(client):
    """The send kick-off returns an ack (how many queued) WITHOUT sending — the
    provider is only touched once the background worker runs."""
    _two_uncategorised(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider()
    ids = _prepare_ids(client, fake)

    with SessionLocal() as db:
        ack = ai_service.cloud_batch_start(db, approve_ids=ids)

    assert ack["queued"] == 2
    assert ack["rejected"] == 0
    assert fake.calls == []  # nothing dispatched yet
    with SessionLocal() as db:  # rows still pending, awaiting the worker
        assert all(db.get(AIRequest, rid).status == "pending" for rid in ids)


def test_background_worker_completes_and_status_reaches_done(client):
    _two_uncategorised(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider(category="Groceries", confidence=0.9)
    ids = _prepare_ids(client, fake)

    with SessionLocal() as db:
        ack = ai_service.cloud_batch_start(db, approve_ids=ids)
    # Mid-flight (before the worker runs): still pending, marked running.
    with SessionLocal() as db:
        mid = ai_service.cloud_batch_status(db, ids)
    assert mid["done"] is False
    assert mid["pending"] == 2
    assert mid["sent"] == 0
    assert mid["running"] is True

    ai_service.run_cloud_batch(ack["queue"], provider=fake)

    assert len(fake.calls) == 2
    with SessionLocal() as db:
        done = ai_service.cloud_batch_status(db, ids)
    assert done["done"] is True
    assert done["sent"] == 2
    assert done["pending"] == 0
    assert done["failed"] == 0
    assert done["running"] is False
    assert len(done["suggestions"]) == 2
    assert done["suggestions"][0]["category_name"] == "Groceries"


def test_no_double_send_while_batch_running(client):
    """A second start for the same rows queues nothing (the ids are reserved
    in-flight), and re-running the worker doesn't re-dispatch a sent row."""
    _two_uncategorised(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider()
    ids = _prepare_ids(client, fake)

    with SessionLocal() as db:
        first = ai_service.cloud_batch_start(db, approve_ids=ids)
    assert first["queued"] == 2
    # Re-triggering while the ids are reserved queues nothing.
    with SessionLocal() as db:
        second = ai_service.cloud_batch_start(db, approve_ids=ids)
    assert second["queued"] == 0

    ai_service.run_cloud_batch(first["queue"], provider=fake)
    assert len(fake.calls) == 2
    # Re-running the worker over the same ids sends nothing more (already completed).
    ai_service.run_cloud_batch(ids, provider=fake)
    assert len(fake.calls) == 2


def test_background_worker_reapplies_never_cloud_guard(client):
    """Per-item send-time re-validation still fires in the background path: a txn
    whose category became never-cloud after staging is recorded failed, not sent."""
    from app.models import Category

    _two_uncategorised(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider()
    ids = _prepare_ids(client, fake)
    with SessionLocal() as db:
        req0 = db.get(AIRequest, ids[0])
        never = Category(name="Therapy", privacy_sensitivity="never_cloud", is_active=True)
        db.add(never)
        db.flush()
        db.get(Transaction, req0.transaction_id).category_id = never.id
        db.commit()

    ai_service.run_cloud_batch(ids, provider=fake)

    assert len(fake.calls) == 1  # only the still-safe request left the device
    with SessionLocal() as db:
        assert db.get(AIRequest, ids[0]).status == "failed"
        status = ai_service.cloud_batch_status(db, ids)
    assert status["failed"] == 1
    assert status["sent"] == 1
    assert status["done"] is True


def test_background_worker_refused_when_mode_now_off(client):
    """If AI is switched off after staging, the background worker sends nothing."""
    _two_uncategorised(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider()
    ids = _prepare_ids(client, fake)

    _set_mode(client, "strict_local")  # AI off between staging and sending
    ai_service.run_cloud_batch(ids, provider=fake)

    assert fake.calls == []
    with SessionLocal() as db:
        status = ai_service.cloud_batch_status(db, ids)
    assert status["failed"] == 2
    assert status["done"] is True


def test_send_endpoint_returns_queued_without_blocking(client, monkeypatch):
    """The /cloud-batch/send route returns the queued ack immediately and schedules
    the background worker (stubbed here so no dispatch happens), and the status
    route reports the in-flight batch."""
    _two_uncategorised(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider()
    ids = _prepare_ids(client, fake)

    scheduled: list[list[int]] = []
    monkeypatch.setattr(ai_service, "run_cloud_batch", lambda queue, **_: scheduled.append(queue))

    res = client.post("/api/ai/cloud-batch/send", json={"approve_ids": ids})
    assert res.status_code == 200
    body = res.json()
    assert body == {"queued": 2, "rejected": 0}
    assert scheduled == [ids]  # worker was scheduled with the queued ids

    ids_qs = ",".join(str(i) for i in ids)
    status = client.get(f"/api/ai/cloud-batch/status?ids={ids_qs}").json()
    assert status["pending"] == 2
    assert status["sent"] == 0
    assert status["done"] is False
    assert status["running"] is True
