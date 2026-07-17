"""Security-health panel + failed-unlock tracking (Stage 12-S3; #128/#130)."""

from __future__ import annotations

from app.models import Household, User
from app.services import security_health_service, security_service
from app.services.household_service import get_or_create_default_household


def _normalise_unlocks():
    """Clear any failed-unlock state leaked from another test (shared temp dir)."""
    security_service.record_successful_unlock()


def _check(checks: list[dict], cid: str) -> dict | None:
    return next((c for c in checks if c["id"] == cid), None)


def test_failed_unlock_tracking():
    _normalise_unlocks()
    assert security_service.failed_unlock_summary()["recent"] == 0

    security_service.record_failed_unlock()
    n = security_service.record_failed_unlock()
    assert n == 2
    summary = security_service.failed_unlock_summary()
    assert summary["recent"] == 2
    assert summary["last_attempt_at"] is not None

    security_service.record_successful_unlock()
    assert security_service.failed_unlock_summary()["recent"] == 0


def test_concurrent_failed_unlocks_are_not_lost():
    """SR-7: the read-modify-write is locked and the file written atomically, so
    concurrent failed-unlock records can't lose updates — the brute-force counter the
    security panel relies on must not *under*count. 30 concurrent records → exactly 30
    stored, and the file is still valid JSON afterwards."""
    import threading

    _normalise_unlocks()  # start from a clean streak
    n = 30  # < _MAX_STORED_UNLOCK_EVENTS, so nothing is capped away
    threads = [threading.Thread(target=security_service.record_failed_unlock) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    events = security_service._read_events()  # valid JSON ⇒ atomic write held
    assert len(events.get("failed_unlocks", [])) == n  # none lost ⇒ locked RMW held
    security_service.record_successful_unlock()  # cleanup for other tests


def test_health_flags_missing_mfa_and_dismiss(client):
    _normalise_unlocks()
    health = client.get("/api/security/health").json()
    mfa = _check(health["checks"], "mfa")
    assert mfa is not None and mfa["severity"] == "warn" and mfa["active"] is True
    before = health["active_count"]
    assert before >= 1

    # Dismiss it → no longer active.
    d = client.post("/api/security/health/dismiss", json={"check_id": "mfa"}).json()
    assert d["dismissed"] is True
    health2 = client.get("/api/security/health").json()
    assert _check(health2["checks"], "mfa")["active"] is False
    assert health2["active_count"] == before - 1

    # Clear the dismissal → active again.
    client.post("/api/security/health/dismiss", json={"check_id": "mfa", "clear": True})
    assert _check(client.get("/api/security/health").json()["checks"], "mfa")["active"] is True


def test_health_snooze_sets_until(client):
    _normalise_unlocks()
    d = client.post("/api/security/health/dismiss", json={"check_id": "mfa", "snooze_days": 7}).json()
    assert d["dismissed"] is True
    assert d["snoozed_until"] is not None
    # Snoozed checks aren't active.
    assert _check(client.get("/api/security/health").json()["checks"], "mfa")["active"] is False
    client.post("/api/security/health/dismiss", json={"check_id": "mfa", "clear": True})


def test_failed_unlocks_surface_as_a_warning(client):
    _normalise_unlocks()
    for _ in range(3):
        security_service.record_failed_unlock()
    fu = _check(client.get("/api/security/health").json()["checks"], "failed_unlocks")
    assert fu is not None and fu["severity"] == "warn" and fu["active"] is True
    security_service.record_successful_unlock()  # cleanup for other tests


def _owner(db, household_id, *, mfa=False, name="O"):
    u = User(household_id=household_id, display_name=name, role="owner",
             status="approved", is_active=True, mfa_enabled=mfa)
    db.add(u)
    db.flush()
    return u


def test_mfa_check_covers_all_owners_not_just_caller(db):
    """SR-E6: the MFA posture must reflect every owner. A co-owner without a second
    factor is a gap even when the caller has MFA on — the check must still warn."""
    hh = get_or_create_default_household(db)
    caller = _owner(db, hh.id, mfa=True, name="Caller")
    _owner(db, hh.id, mfa=False, name="CoOwner")  # co-owner without MFA
    db.commit()

    health = security_health_service.evaluate(db, caller)
    mfa = _check(health["checks"], "mfa")
    assert mfa is not None and mfa["severity"] == "warn" and mfa["active"] is True


def test_mfa_check_ok_when_all_owners_enrolled(db):
    hh = get_or_create_default_household(db)
    caller = _owner(db, hh.id, mfa=True, name="Caller")
    _owner(db, hh.id, mfa=True, name="CoOwner")
    db.commit()

    mfa = _check(security_health_service.evaluate(db, caller)["checks"], "mfa")
    assert mfa is not None and mfa["severity"] == "ok" and mfa["active"] is False


def test_pending_count_is_household_scoped(db):
    """SR-E6: users awaiting approval must be counted within the caller's household —
    a pending user in another household must not surface on this owner's panel."""
    hh = get_or_create_default_household(db)
    caller = _owner(db, hh.id, mfa=True, name="Caller")
    # A pending user in a *different* household must be excluded.
    other_hh = Household(name="Other", currency="GBP", mode="household")
    db.add(other_hh)
    db.flush()
    db.add(User(household_id=other_hh.id, display_name="Stranger", role="member",
                status="pending", is_active=True))
    db.commit()

    assert _check(security_health_service.evaluate(db, caller)["checks"], "pending_users") is None

    # A pending user in the caller's own household does surface.
    db.add(User(household_id=hh.id, display_name="Newbie", role="member",
                status="pending", is_active=True))
    db.commit()
    assert _check(security_health_service.evaluate(db, caller)["checks"], "pending_users") is not None


def test_health_is_owner_only(client):
    _normalise_unlocks()
    client.get("/api/users/me")  # bootstrap the local owner first
    member = {"X-Remote-User-Id": "ha-mike", "X-Remote-User-Display-Name": "Mike"}
    client.get("/api/users/me", headers=member)  # appears pending
    mike_id = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == "ha-mike")
    client.patch(f"/api/users/{mike_id}", json={"role": "member", "status": "approved"})

    assert client.get("/api/security/health", headers=member).status_code == 403
