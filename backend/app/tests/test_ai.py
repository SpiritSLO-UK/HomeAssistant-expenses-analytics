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

    def __init__(self, category="Groceries", confidence=0.9, country=None, vendor=None):
        self.category = category
        self.confidence = confidence
        self.country = country
        self.vendor = vendor
        self.calls: list[dict] = []

    def available(self) -> bool:
        return True

    def classify_transaction(self, description, amount, currency, candidate_categories):
        self.calls.append({"description": description, "candidate_categories": candidate_categories})
        return {"category": self.category, "confidence": self.confidence,
                "rationale": "because", "country": self.country, "vendor": self.vendor}


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


def _hdr(uid, name=None):
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}


def _approve_member(client, uid, name):
    client.get("/api/users/me", headers=_hdr(uid, name))
    row = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == uid)
    client.patch(f"/api/users/{row}", json={"role": "member", "status": "approved"})
    return row


# --- gating ---

def test_classify_returns_inferred_country(client):
    txn_id = _import_txn(client, desc="EL CORTE INGLES MADRID")
    _set_mode(client, "local_llm")
    res = _classify(txn_id, provider=FakeProvider(country="es"))  # lower-case from the model
    assert res["country"] == "ES"  # normalised to ISO-3166-1 alpha-2


def test_classify_drops_invalid_country(client):
    txn_id = _import_txn(client)
    _set_mode(client, "local_llm")
    # A full name / prose is not a valid alpha-2 code → dropped.
    assert _classify(txn_id, provider=FakeProvider(country="Spain"))["country"] is None
    assert _classify(txn_id, provider=FakeProvider(country=None))["country"] is None


def test_classify_returns_suggested_vendor(client):
    txn_id = _import_txn(client)
    _set_mode(client, "local_llm")
    assert _classify(txn_id, provider=FakeProvider(vendor="  Tesco  "))["vendor"] == "Tesco"  # trimmed
    # Empty / 'null' / non-string → dropped.
    assert _classify(txn_id, provider=FakeProvider(vendor="null"))["vendor"] is None
    assert _classify(txn_id, provider=FakeProvider(vendor=None))["vendor"] is None


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
    # A card number is sensitive, so cloud_auto refuses to auto-send it (CR-SEC-10);
    # the redaction still runs on the cloud_manual approval payload the user previews.
    txn_id = _import_txn(client, desc="CARD 4111 1111 1111 1111 PAYMENT")
    _set_mode(client, "cloud_manual")
    fake = FakeProvider()
    first = _classify(txn_id, provider=fake)  # staged for approval, nothing sent yet
    assert first["status"] == "approval_required"
    assert fake.calls == []
    sent = first["payload"]["description"]
    assert "[card]" in sent          # redacted before leaving the device
    assert "4111 1111 1111" not in sent


def test_cloud_auto_refuses_sensitive_uncategorised(client):
    # CR-SEC-10 + SR-D1: an *uncategorised* row whose raw text looks sensitive must
    # not be auto-sent to cloud — previously the gate only fired for categorised rows.
    txn_id = _import_txn(client, desc="ZZQ 12345678 REF")  # 8-digit account-ish run
    _set_mode(client, "cloud_auto")
    fake = FakeProvider()
    with pytest.raises(AIDisabled):
        _classify(txn_id, provider=fake)
    assert fake.calls == []


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


def test_run_request_refuses_when_cloud_mode_revoked(client):
    # SR-D1: a payload staged under a cloud mode must be re-validated at send time —
    # switching to a non-cloud mode afterwards must block the send.
    txn_id = _import_txn(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider()
    rid = _classify(txn_id, provider=fake)["ai_request_id"]
    _set_mode(client, "local_llm")  # no longer a cloud mode
    with SessionLocal() as db:
        req = db.get(AIRequest, rid)
        with pytest.raises(AIDisabled):
            ai_service.run_request(db, req, provider=fake)
    assert fake.calls == []


def test_run_request_rechecks_never_cloud_category(client):
    # SR-D1: the target category may be marked never-cloud after staging — re-check it.
    txn_id = _import_txn(client)
    _set_mode(client, "cloud_manual")
    fake = FakeProvider()
    rid = _classify(txn_id, provider=fake)["ai_request_id"]
    with SessionLocal() as db:
        cat = db.scalars(select(Category)).first()
        cat.privacy_sensitivity = "never_cloud"
        db.get(Transaction, txn_id).category_id = cat.id
        db.commit()
        req = db.get(AIRequest, rid)
        with pytest.raises(AIDisabled):
            ai_service.run_request(db, req, provider=fake)
    assert fake.calls == []


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


def test_classify_batch_survives_unexpected_error(client):
    # SR-D1: a non-AIError raised for one item must not abort the whole batch.
    _import_rows(client, [("2026-05-02", "ZZQ MARKET", "-12.00"), ("2026-05-03", "QQX DEPOT", "-8.00")])
    _set_mode(client, "local_llm")

    class _Flaky(FakeProvider):
        def __init__(self):
            super().__init__(category="Groceries", confidence=0.9)
            self._n = 0

        def classify_transaction(self, **kwargs):
            self._n += 1
            if self._n == 1:
                raise RuntimeError("boom")  # NOT an AIError
            return super().classify_transaction(**kwargs)

    with SessionLocal() as db:
        res = ai_service.classify_batch(db, provider=_Flaky())
    assert res["considered"] == 2
    assert res["count"] == 1  # first item blew up, second still succeeded


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


def test_apply_skips_txn_in_out_of_scope_account(client):
    """#17 IDOR: a member cannot apply a category onto a transaction in an account
    they can't see — it is skipped (never written); the owner is unrestricted."""
    from datetime import date
    from decimal import Decimal

    from app.models import Account

    client.get("/api/users/me")  # headerless → local owner
    bob = _approve_member(client, "ha-bob", "Bob")
    _approve_member(client, "ha-alice", "Alice")
    groceries = next(c["id"] for c in client.get("/api/categories").json() if c["name"] == "Groceries")
    with SessionLocal() as db:
        priv = Account(name="Bob Private", account_type="current_account", currency="GBP",
                       owner_user_id=bob, is_shared=False)
        db.add(priv)
        db.flush()
        txn = Transaction(account_id=priv.id, transaction_date=date(2026, 5, 15),
                          description_raw="BOB SECRET", amount=Decimal("-9.00"), currency="GBP",
                          direction="debit", base_amount=Decimal("-9.00"), fx_rate=Decimal("1"))
        db.add(txn)
        db.commit()
        txn_id = txn.id
    body = {"items": [{"transaction_id": txn_id, "category_id": groceries}]}
    # Alice (member) can't see Bob's private account → the write is skipped.
    r = client.post("/api/ai/apply", json=body, headers=_hdr("ha-alice", "Alice"))
    assert r.status_code == 200
    assert r.json()["applied"] == 0
    with SessionLocal() as db:
        assert db.get(Transaction, txn_id).category_id is None
    # Owner is unrestricted → the identical apply now writes it.
    assert client.post("/api/ai/apply", json=body).json()["applied"] == 1


def test_apply_suggestions_resolves_review_item(client):
    """A bulk apply clears the unknown-category review item for the row, like the
    per-row categorise does (so the Review Queue's review tab stays coherent)."""
    from app.models import ReviewItem
    from app.services import review_service

    txn_id = _import_txn(client)
    groceries = next(c["id"] for c in client.get("/api/categories").json() if c["name"] == "Groceries")
    with SessionLocal() as db:
        review_service.add(
            db, item_type="transaction", item_id=txn_id, reason="unknown_category",
            severity="info", suggested_action="Categorise this transaction.",
        )
        db.commit()
    client.post("/api/ai/apply", json={"items": [{"transaction_id": txn_id, "category_id": groceries}]})
    with SessionLocal() as db:
        still_open = db.scalars(
            select(ReviewItem).where(ReviewItem.item_id == txn_id, ReviewItem.status == "open")
        ).all()
        assert still_open == []


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


def test_extract_receipt_image_resolves_category(db, monkeypatch):
    # The same vision call also returns a category, resolved to a candidate id (#110).
    from app.models import Category
    cat = Category(name="Groceries", is_active=True)
    db.add(cat)
    db.commit()
    _mode(db, "local_llm")
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _VisionProvider(
        {"merchant": "Tesco", "total": "12.34", "currency": "GBP", "category": "Groceries"}))
    out = ai_service.extract_receipt_image(db, b"img", "image/jpeg")
    assert out["category_id"] == cat.id
    assert out["category_name"] == "Groceries"


def test_extract_image_off_is_disabled(db):
    with pytest.raises(AIDisabled):
        ai_service.extract_statement_image(db, b"img", "image/png")  # strict_local default


def test_extract_image_cloud_auto_refused_without_approval(db, monkeypatch):
    # CR-SEC-10: a raw image can't be redacted, so cloud_auto refuses to auto-send it
    # unless the request is explicitly approved.
    _mode(db, "cloud_auto")
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _VisionProvider({"transactions": []}))
    with pytest.raises(AIDisabled):
        ai_service.extract_statement_image(db, b"\x89PNG", "image/png")
    # Explicit per-request approval lets it through.
    assert ai_service.extract_statement_image(db, b"\x89PNG", "image/png", approved=True) == []


def test_extract_image_size_capped(db, monkeypatch):
    # SR-D1: an over-large image is refused before it reaches the provider.
    _mode(db, "local_llm")
    called = {"hit": False}

    class _NeverCalled(_VisionProvider):
        def extract_from_image(self, *a, **k):  # pragma: no cover - must not run
            called["hit"] = True
            return {}

    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _NeverCalled(None))
    big = b"x" * (15 * 1024 * 1024 + 1)
    with pytest.raises(AIDisabled):
        ai_service.extract_statement_image(db, big, "image/png")
    assert called["hit"] is False


def test_extract_image_rejects_non_dict_result(db, monkeypatch):
    # SR-D1: a non-object vision reply is rejected instead of blowing up on .get(...).
    from app.services.ai_provider import AIError
    _mode(db, "local_llm")
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _VisionProvider(["not", "a", "dict"]))
    with pytest.raises(AIError):
        ai_service.extract_statement_image(db, b"img", "image/png")


def test_extract_statement_image_drops_non_dict_rows(db, monkeypatch):
    _mode(db, "local_llm")
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _VisionProvider(
        {"transactions": [{"date": "2026-06-01", "description": "Tesco", "amount": "-1"}, "junk", 42]}))
    rows = ai_service.extract_statement_image(db, b"img", "image/png")
    assert rows == [{"date": "2026-06-01", "description": "Tesco", "amount": "-1"}]


def test_ai_extract_import_route_creates_import(client, monkeypatch):
    client.get("/api/users/me")
    client.put("/api/settings", json={"privacy_mode": "cloud_manual", "ai_provider": "openai_compatible",
                                      "ai_base_url": "https://x/v1", "ai_model": "m"})
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _VisionProvider(
        {"transactions": [{"date": "2026-06-02", "description": "ACME", "amount": "-9.99"}]}))
    r = client.post("/api/imports/ai-extract", files={"file": ("s.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["rows_detected"] == 1


def test_ai_extract_import_confirm_persists(client, monkeypatch):
    """Regression: an AI image-extract import must be confirmable end-to-end.
    Previously confirm raised 'Unknown parser: ai_image_extract' because the
    pseudo-parser couldn't be re-resolved and the image isn't re-parseable."""
    client.get("/api/users/me")
    client.put("/api/settings", json={"privacy_mode": "cloud_manual", "ai_provider": "openai_compatible",
                                      "ai_base_url": "https://x/v1", "ai_model": "m"})
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _VisionProvider(
        {"transactions": [{"date": "2026-06-02", "description": "Post Office", "amount": "-10.95"}]}))
    r = client.post("/api/imports/ai-extract", files={"file": ("s.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert r.status_code == 200, r.text
    import_id = r.json()["import_id"]

    confirm = client.post(f"/api/imports/{import_id}/confirm")
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["status"] == "imported"
    assert confirm.json()["report"]["new"] == 1

    txns = client.get("/api/transactions").json()
    assert any(t["description_raw"] == "Post Office" for t in txns["items"])


def test_ai_extract_import_off_returns_400(client):
    client.get("/api/users/me")  # strict_local default → AI off
    r = client.post("/api/imports/ai-extract", files={"file": ("s.png", b"x", "image/png")})
    assert r.status_code == 400


# --- PDF receipts/invoices for vision extraction ---


def test_render_pdf_page_png(samples_dir):
    """The PDF→PNG rasteriser turns a PDF's first page into a real PNG so it can
    be sent to vision AI (receipts/invoices are often PDFs)."""
    from app.services import ocr_service

    pdf = next((samples_dir.parent / "sample-pdf").glob("*.pdf"))
    png = ocr_service.render_pdf_page_png(pdf)
    assert png is not None and png[:4] == b"\x89PNG"


def test_ai_extract_accepts_pdf_receipt(client, monkeypatch, samples_dir):
    """A PDF receipt is no longer rejected — the first page is rendered + sent to
    vision AI, and the returned fields are applied."""
    client.get("/api/users/me")
    client.put("/api/settings", json={"privacy_mode": "cloud_manual", "ai_provider": "openai_compatible",
                                      "ai_base_url": "https://x/v1", "ai_model": "m"})
    monkeypatch.setattr(ai_service, "get_provider", lambda _db: _VisionProvider(
        {"merchant": "Acme Ltd", "date": "2026-06-01", "total": "42.00", "currency": "GBP"}))
    pdf = next((samples_dir.parent / "sample-pdf").glob("*.pdf"))
    up = client.post("/api/receipts/upload", files={"file": ("invoice.pdf", pdf.read_bytes(), "application/pdf")})
    assert up.status_code == 201, up.text
    rid = up.json()["id"]
    r = client.post(f"/api/receipts/{rid}/ai-extract")
    assert r.status_code == 200, r.text
    assert "Acme Ltd" in r.text  # the AI-extracted merchant was applied
