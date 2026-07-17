"""Data retention & expiration (backlog #78, #147).

Covers the engine (validate / preview / archive-then-purge / auto vs manual),
receipt original-drop, failed-unlock pruning, backup safety + trim, the log
viewers hiding archived rows, the security-health notification, and owner-gating.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models import AIRequest, AuditLog, Category, Receipt, Transaction, User
from app.services import (
    ai_service,
    backup_service,
    receipt_service,
    retention_service,
    security_health_service,
    security_service,
    settings_service,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _events_path() -> Path:
    return settings.database_file.parent / "security_events.json"


@pytest.fixture(autouse=True)
def _clean_files():
    """Isolate the on-disk state these tests touch (shared temp data dir)."""
    for d in (backup_service.backups_dir(), receipt_service.receipts_dir()):
        for f in d.glob("*"):
            f.unlink(missing_ok=True)
    _events_path().unlink(missing_ok=True)
    yield


def _ai(db, days: int) -> AIRequest:
    r = AIRequest(
        provider="p",
        task_type="classify_transaction",
        privacy_mode="local_llm",
        created_at=_now() - timedelta(days=days),
    )
    db.add(r)
    db.commit()
    return r


def _audit(db, days: int, action: str = "seed") -> AuditLog:
    e = AuditLog(actor="x", action=action, created_at=_now() - timedelta(days=days))
    db.add(e)
    db.commit()
    return e


def _set(db, partial: dict) -> None:
    retention_service.save_policy(db, retention_service.validate_policy(partial))


# --- validation ----------------------------------------------------------

def test_validate_policy_rejects_bad_input():
    with pytest.raises(ValueError):
        retention_service.validate_policy({"ai_requests": {"archive_after_days": -1}})
    with pytest.raises(ValueError):
        retention_service.validate_policy({"nope": {"purge_after_days": 5}})
    with pytest.raises(ValueError):
        retention_service.validate_policy({"ai_requests": {"archive_after_days": 30, "purge_after_days": 10}})
    with pytest.raises(ValueError):
        retention_service.validate_policy({"audit_logs": {"auto_purge": "yes"}})


def test_validate_policy_normalises():
    pol = retention_service.validate_policy({"ai_requests": {"purge_after_days": 90, "auto_purge": True}})
    assert pol["ai_requests"]["purge_after_days"] == 90
    assert pol["ai_requests"]["auto_purge"] is True
    assert pol["audit_logs"]["purge_after_days"] is None
    # failed_unlock is purge-only — no archive field.
    assert "archive_after_days" not in pol["failed_unlock"]


# --- preview + archive/purge ---------------------------------------------

def test_preview_is_a_plan_without_mutating(db):
    for d in (400, 100, 10):
        _ai(db, d)
    _set(db, {"ai_requests": {"archive_after_days": 30, "purge_after_days": 365}})
    plan = retention_service.preview(db)
    assert plan["ai_requests"]["archive_due"] == 2  # 400d + 100d
    assert plan["ai_requests"]["purge_due"] == 1    # 400d
    assert plan["pending_purge"] == 1               # auto_purge off → awaiting confirm
    # Preview did not change anything.
    assert len(ai_service.list_requests(db, include_archived=True)) == 3


def test_run_archives_then_purges_ai_logs(db):
    for d in (400, 100, 10):
        _ai(db, d)
    _set(db, {"ai_requests": {"archive_after_days": 30, "purge_after_days": 365}})
    result = retention_service.run(db, actor="test", purge_mode="all")
    assert result["counts"]["ai_requests"]["archived"] == 2
    assert result["counts"]["ai_requests"]["purged"] == 1
    assert result["backup_taken"] is True
    # 400d purged; 100d archived (hidden); 10d active.
    assert len(ai_service.list_requests(db)) == 1
    assert len(ai_service.list_requests(db, include_archived=True)) == 2
    # The purge took a timestamped safety backup.
    assert len(list(backup_service.backups_dir().glob("retention-*.db"))) == 1


def test_auto_mode_only_purges_auto_types(db):
    _ai(db, 400)
    _audit(db, 400, action="seed")
    _set(db, {
        "ai_requests": {"purge_after_days": 30, "auto_purge": True},
        "audit_logs": {"purge_after_days": 30, "auto_purge": False},
    })
    retention_service.run(db, actor="system", purge_mode="auto")
    # ai_requests purged (auto); audit 'seed' kept (awaiting confirm).
    assert len(ai_service.list_requests(db, include_archived=True)) == 0
    kept = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "seed"))
    assert kept == 1


def test_manual_run_purges_all_due_types(db):
    _ai(db, 400)
    _audit(db, 400, action="seed")
    _set(db, {
        "ai_requests": {"purge_after_days": 30},
        "audit_logs": {"purge_after_days": 30},
    })
    retention_service.run(db, actor="owner", purge_mode="all")
    assert len(ai_service.list_requests(db, include_archived=True)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "seed")) == 0


def test_off_by_default_is_a_noop(db):
    _ai(db, 400)
    _audit(db, 400, action="seed")
    result = retention_service.run(db, actor="system", purge_mode="auto")
    assert all(c["archived"] == 0 and c["purged"] == 0 for c in result["counts"].values())
    assert result["backup_taken"] is False
    assert len(ai_service.list_requests(db, include_archived=True)) == 1


# --- receipts -------------------------------------------------------------

def test_receipt_archive_drops_file_keeps_row(db):
    r, _ = receipt_service.store_upload(db, "arch.jpg", b"archive-me")
    path = r.storage_path
    r.receipt_date = date.today() - timedelta(days=400)
    db.commit()
    assert Path(path).exists()
    _set(db, {"receipts": {"archive_after_days": 30}})
    retention_service.run(db, actor="t", purge_mode="auto")
    db.refresh(r)
    assert r.archived_at is not None
    assert r.storage_path is None
    assert not Path(path).exists()
    # The row + extracted fields survive.
    assert db.get(Receipt, r.id) is not None


def test_receipt_purge_deletes_row_and_file(db):
    r, _ = receipt_service.store_upload(db, "purge.jpg", b"purge-me")
    rid, path = r.id, r.storage_path
    r.receipt_date = date.today() - timedelta(days=400)
    db.commit()
    _set(db, {"receipts": {"purge_after_days": 30, "auto_purge": True}})
    retention_service.run(db, actor="t", purge_mode="auto")
    assert db.get(Receipt, rid) is None
    assert not Path(path).exists()


def _txn(db) -> Transaction:
    t = Transaction(
        transaction_date=date.today(),
        description_raw="shop",
        amount=Decimal("5.00"),
        currency="GBP",
        direction="debit",
    )
    db.add(t)
    db.commit()
    return t


def test_receipt_original_dropped_on_confirm_when_enabled(db):
    # delete-after-processing now defaults OFF, so opt in explicitly to test the drop.
    settings_service.set_value(db, settings_service.RECEIPT_DELETE_AFTER_PROCESSING, "true")
    r, _ = receipt_service.store_upload(db, "conf.jpg", b"confirm-me")
    path = r.storage_path
    txn = _txn(db)
    receipt_service.confirm_match(db, r, txn.id)
    db.refresh(r)
    assert r.storage_path is None
    assert not Path(path).exists()
    assert db.get(Receipt, r.id) is not None  # row kept


def test_receipt_kept_on_confirm_by_default(db):
    # New default (keep originals so they stay viewable) — no setting touched.
    r, _ = receipt_service.store_upload(db, "default.jpg", b"keep-me-default")
    path = r.storage_path
    txn = _txn(db)
    receipt_service.confirm_match(db, r, txn.id)
    db.refresh(r)
    assert r.storage_path == path
    assert Path(path).exists()


def test_receipt_kept_on_confirm_when_disabled(db):
    settings_service.set_value(db, settings_service.RECEIPT_DELETE_AFTER_PROCESSING, "false")
    r, _ = receipt_service.store_upload(db, "keep.jpg", b"keep-me")
    path = r.storage_path
    txn = _txn(db)
    receipt_service.confirm_match(db, r, txn.id)
    db.refresh(r)
    assert r.storage_path == path
    assert Path(path).exists()


# --- failed-unlock pruning ------------------------------------------------

def test_prune_failed_unlocks_by_age():
    _events_path().write_text(
        json.dumps({"failed_unlocks": [
            (_now() - timedelta(days=100)).isoformat(),
            (_now() - timedelta(days=1)).isoformat(),
        ]}),
        encoding="utf-8",
    )
    assert security_service.count_failed_unlocks_older_than(30) == 1
    assert security_service.prune_failed_unlocks(30) == 1
    assert security_service.count_failed_unlocks_older_than(30) == 0


# --- backup trim ----------------------------------------------------------

def test_prune_backups_honours_age_and_min_keep(db):
    d = backup_service.backups_dir()
    now = time.time()
    for i in range(3):
        p = d / f"old-{i}.db"
        p.write_bytes(b"x" * 1000)
        os.utime(p, (now - 100 * 86400, now - 100 * 86400))
    for i in range(2):
        p = d / f"new-{i}.db"
        p.write_bytes(b"x" * 1000)
        os.utime(p, (now, now))
    settings_service.set_value(db, settings_service.BACKUP_MIN_KEEP, "2")
    settings_service.set_value(db, settings_service.BACKUP_MAX_AGE_DAYS, "30")
    settings_service.set_value(db, settings_service.BACKUP_MAX_TOTAL_MB, "999")
    res = backup_service.prune_backups(db)
    assert res["removed"] == 3  # the three 100-day-old files
    assert res["kept"] == 2     # the two recent (protected by min_keep)


def test_prune_backups_honours_min_keep_over_size(db):
    d = backup_service.backups_dir()
    now = time.time()
    for i in range(3):
        p = d / f"b-{i}.db"
        p.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB each, way over a 1 MB cap
        os.utime(p, (now - i, now - i))  # distinct mtimes
    settings_service.set_value(db, settings_service.BACKUP_MIN_KEEP, "2")
    settings_service.set_value(db, settings_service.BACKUP_MAX_AGE_DAYS, "9999")
    settings_service.set_value(db, settings_service.BACKUP_MAX_TOTAL_MB, "1")
    res = backup_service.prune_backups(db)
    # min_keep=2 protects the two newest; only the oldest is deletable for size.
    assert res["kept"] == 2
    assert res["removed"] == 1


def test_prune_backups_size_cap_removes_multiple(db):
    """Size cap deletes several oldest survivors in one pass (O(n) running total)."""
    d = backup_service.backups_dir()
    now = time.time()
    for i in range(5):
        p = d / f"s-{i}.db"
        p.write_bytes(b"x" * (1024 * 1024))  # 1 MB each
        os.utime(p, (now - i, now - i))  # distinct mtimes, s-0 newest
    settings_service.set_value(db, settings_service.BACKUP_MIN_KEEP, "1")
    settings_service.set_value(db, settings_service.BACKUP_MAX_AGE_DAYS, "9999")
    settings_service.set_value(db, settings_service.BACKUP_MAX_TOTAL_MB, "2")
    res = backup_service.prune_backups(db)
    # Keep the newest until under the 2 MB cap: s-0 (protected) + s-1 = 2 MB.
    assert res["kept"] == 2
    assert res["removed"] == 3


def test_import_config_rolls_back_on_failure(db, monkeypatch):
    """A failure mid-import must leave no partial rows behind (atomic transaction)."""
    before = db.scalar(select(func.count()).select_from(Category))
    data = {
        "categories": [{"name": "ImportRollbackCat"}],
        "vendors": [],
        "settings": [],
    }

    def _boom(*_args, **_kwargs):
        raise RuntimeError("settings import blew up")

    monkeypatch.setattr(
        backup_service.settings_service, "apply_imported_settings", _boom
    )
    with pytest.raises(RuntimeError):
        backup_service.import_config(db, data)

    db.rollback()  # drop any session-local state before re-querying
    after = db.scalar(select(func.count()).select_from(Category))
    assert after == before
    assert (
        db.scalar(
            select(func.count())
            .select_from(Category)
            .where(Category.name == "ImportRollbackCat")
        )
        == 0
    )


# --- security-health notification ----------------------------------------

def test_security_health_flags_pending_purge(db):
    _ai(db, 400)
    _set(db, {"ai_requests": {"purge_after_days": 30}})  # auto_purge off → pending
    owner = User(display_name="O", role="owner", status="approved", is_active=True)
    db.add(owner)
    db.commit()
    health = security_health_service.evaluate(db, owner)
    assert "retention_pending" in [c["id"] for c in health["checks"]]


# --- API owner-gating + viewer toggle ------------------------------------

def _hdr(uid: str) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": uid}


def test_retention_api_is_owner_only(client):
    client.get("/api/users/me")  # bootstrap owner
    client.get("/api/users/me", headers=_hdr("mem"))  # second user → pending
    uid = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == "mem")
    client.patch(f"/api/users/{uid}", json={"role": "member", "status": "approved"})

    h = _hdr("mem")
    assert client.get("/api/retention/policy", headers=h).status_code == 403
    assert client.put("/api/retention/policy", json={"policy": {}}, headers=h).status_code == 403
    assert client.post("/api/retention/run", headers=h).status_code == 403
    # Owner can read the policy + preview.
    assert client.get("/api/retention/policy").status_code == 200
    assert client.get("/api/retention/preview").status_code == 200


def test_retention_api_validation_and_run(client):
    client.get("/api/users/me")  # owner
    # A bad policy is rejected.
    bad = client.put("/api/retention/policy", json={"policy": {"ai_requests": {"archive_after_days": 9, "purge_after_days": 1}}})
    assert bad.status_code == 400
    # A valid policy round-trips.
    good = client.put("/api/retention/policy", json={
        "policy": {"ai_requests": {"purge_after_days": 30}},
        "receipt_delete_after_processing": False,
    })
    assert good.status_code == 200
    body = good.json()
    assert body["policy"]["ai_requests"]["purge_after_days"] == 30
    assert body["receipt_delete_after_processing"] is False
    # Run with an empty-of-data DB is a clean no-op.
    run = client.post("/api/retention/run")
    assert run.status_code == 200
    assert run.json()["backup_taken"] is False
