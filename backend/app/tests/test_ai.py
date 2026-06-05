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
    assert res["confidence"] == pytest.approx(0.8)
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


# --- re-process (scope=recheck): re-run AI over auto-categorised rows, never manual ---

def test_apply_suggestions_never_overwrites_manual(client):
    txn_id = _import_txn(client)
    cats = client.get("/api/categories").json()
    groceries = next(c["id"] for c in cats if c["name"] == "Groceries")
    eating_out = next(c["id"] for c in cats if c["name"] == "Eating Out")
    # Manual choice -> confidence 1.0 (locked).
    client.post(f"/api/transactions/{txn_id}/categorise", json={"category_id": groceries})
    # A re-process suggestion must not overwrite it.
    r = client.post("/api/ai/apply", json={"items": [{"transaction_id": txn_id, "category_id": eating_out}]})
    assert r.json()["applied"] == 0
    assert client.get(f"/api/transactions/{txn_id}").json()["category_id"] == groceries


def test_recheck_scope_includes_auto_not_manual(client):
    # TESCO -> Groceries by keyword (auto, confidence < 1.0); ZZQ -> uncategorised;
    # MANUAL CO -> manually set (locked).
    _import_rows(client, [
        ("2026-05-02", "TESCO STORES 41", "-20.00"),
        ("2026-05-03", "ZZQ MARKET", "-12.00"),
        ("2026-05-04", "MANUAL CO", "-5.00"),
    ])
    by_desc = {t["description_raw"]: t for t in client.get("/api/transactions").json()["items"]}
    assert by_desc["TESCO STORES 41"]["category_id"] is not None  # keyword auto-categorised
    assert by_desc["ZZQ MARKET"]["category_id"] is None
    groceries = next(c["id"] for c in client.get("/api/categories").json() if c["name"] == "Groceries")
    client.post(f"/api/transactions/{by_desc['MANUAL CO']['id']}/categorise", json={"category_id": groceries})

    _set_mode(client, "local_llm")
    with SessionLocal() as db:
        uncat = ai_service.classify_batch(db, provider=FakeProvider(category="Eating Out"))
        rech = ai_service.classify_batch(db, provider=FakeProvider(category="Eating Out"), scope="recheck")
    # Default: only the uncategorised ZZQ. Re-check: ZZQ + the auto TESCO, never the manual row.
    assert uncat["considered"] == 1
    assert rech["considered"] == 2


def test_classify_batch_rejects_bad_scope(client):
    _set_mode(client, "local_llm")
    assert client.post("/api/ai/classify-batch?scope=everything").status_code == 422


# --- test-connection probe (Settings → AI "Test connection") ---


def test_ai_test_off_by_default(client):
    client.get("/api/users/me")
    body = client.post("/api/ai/test").json()
    assert body["ok"] is False and body["reason"] == "off"


def test_ai_test_not_configured(client):
    client.put("/api/settings", json={"privacy_mode": "local_llm"})  # mode on, but no provider/url/model
    body = client.post("/api/ai/test").json()
    assert body["ok"] is False and body["reason"] == "not_configured"


def test_ai_test_success(client, monkeypatch):
    _set_mode(client, "local_llm")
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: FakeProvider(category="Groceries"))
    body = client.post("/api/ai/test").json()
    assert body["ok"] is True and body["sample_category"] == "Groceries"


def test_ai_test_reports_provider_error(client, monkeypatch):
    from app.services.ai_provider import AIError
    _set_mode(client, "cloud_manual")

    class _Boom(FakeProvider):
        def classify_transaction(self, **kwargs):
            raise AIError("connection refused")

    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _Boom())
    body = client.post("/api/ai/test").json()
    assert body["ok"] is False and body["reason"] == "error" and "connection refused" in body["message"]


# --- already-AI-processed flag (exclude on re-categorise) ---


def test_already_ai_processed_flag(db):
    from datetime import date
    from decimal import Decimal

    def _txn(desc):
        t = Transaction(description_raw=desc, amount=Decimal("-1"), base_amount=Decimal("-1"),
                        currency="GBP", direction="debit", transaction_date=date(2026, 6, 1))
        db.add(t)
        db.flush()
        return t

    done_txn, fresh_txn = _txn("DONE"), _txn("FRESH")
    db.add(AIRequest(transaction_id=done_txn.id, provider="fake", task_type="classify_transaction",
                     privacy_mode="local_llm", approval_status="not_required", status="completed"))
    # A pending (not completed) request must NOT count as processed.
    db.add(AIRequest(transaction_id=fresh_txn.id, provider="fake", task_type="classify_transaction",
                     privacy_mode="cloud_manual", approval_status="pending", status="pending"))
    db.commit()

    done = ai_service._already_ai_processed(db, [done_txn.id, fresh_txn.id])
    assert done_txn.id in done and fresh_txn.id not in done


# --- vision image extraction (Q3) ---


class _VisionProvider:
    name = "vision-fake"
    model = "v"

    def __init__(self, result):
        self._result = result

    def available(self) -> bool:
        return True

    def extract_from_image(self, image_b64, mime, *, system, instruction):
        return self._result


def _mode(db, mode):
    from app.services import settings_service
    settings_service.set_value(db, settings_service.PRIVACY_MODE, mode)


def test_extract_statement_image_returns_rows(db, monkeypatch):
    _mode(db, "cloud_manual")
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _VisionProvider(
        {"transactions": [{"date": "2026-06-01", "description": "Tesco", "amount": "-12.34"}]}))
    rows = ai_service.extract_statement_image(db, b"\x89PNG\r\n", "image/png")
    assert rows == [{"date": "2026-06-01", "description": "Tesco", "amount": "-12.34"}]


def test_extract_receipt_image_returns_fields(db, monkeypatch):
    _mode(db, "local_llm")
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _VisionProvider(
        {"merchant": "Tesco", "date": "2026-06-01", "total": "12.34", "currency": "GBP"}))
    out = ai_service.extract_receipt_image(db, b"img", "image/jpeg")
    assert out["merchant"] == "Tesco" and out["total"] == "12.34"


def test_extract_image_off_is_disabled(db):
    with pytest.raises(AIDisabled):
        ai_service.extract_statement_image(db, b"img", "image/png")  # strict_local default


def test_ai_extract_import_route_creates_import(client, monkeypatch):
    client.get("/api/users/me")
    client.put("/api/settings", json={"privacy_mode": "cloud_manual", "ai_provider": "openai_compatible",
                                      "ai_base_url": "http://x/v1", "ai_model": "m"})
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _VisionProvider(
        {"transactions": [{"date": "2026-06-02", "description": "ACME", "amount": "-9.99"}]}))
    r = client.post("/api/imports/ai-extract", files={"file": ("s.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["rows_detected"] == 1


def test_ai_extract_import_off_returns_400(client):
    client.get("/api/users/me")  # strict_local default → AI off
    r = client.post("/api/imports/ai-extract", files={"file": ("s.png", b"x", "image/png")})
    assert r.status_code == 400
